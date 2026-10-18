"""Real pinned MSAL and generated Graph SDK directory qualification; local profiles only."""

import argparse
import asyncio
import html
import json
import re
import secrets
import ssl
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import msal
import requests
from azure.core.credentials import AccessToken
from kiota_abstractions.api_error import APIError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)
from msgraph import GraphServiceClient
from msgraph.generated.models.group import Group
from msgraph.generated.models.password_profile import PasswordProfile
from msgraph.generated.models.reference_create import ReferenceCreate
from msgraph.generated.models.user import User
from msgraph.generated.users.users_request_builder import UsersRequestBuilder
from msgraph.graph_request_adapter import GraphRequestAdapter, options
from msgraph_core import GraphClientFactory

try:
    from .mfa_form import mfa_form_values
except ImportError:  # Direct script execution in the isolated client container.
    from mfa_form import mfa_form_values


def acquire(config):
    authority = config["endpoints"]["login"] + "/" + config["tenant_id"]
    result = {}
    with requests.Session() as transport:
        transport.verify, transport.trust_env = config["ca_file"], False
        for label, identifier, secret_file in (
            ("application", config["client_id"], config["secret_file"]),
            ("denied_application", config["negative_client_id"], config["negative_secret_file"]),
        ):
            app = msal.ConfidentialClientApplication(
                identifier,
                client_credential=json.loads(Path(secret_file).read_text())["secret"],
                authority=authority,
                instance_discovery=False,
                validate_authority=False,
                http_client=transport,
            )
            tokens = app.acquire_token_for_client(["https://graph.microsoft.com/.default"])
            assert "access_token" in tokens, tokens.get("error")
            result[label] = tokens["access_token"]
        for label, scopes in (
            ("delegated", config["delegated_scopes"]),
            ("denied_delegated", ["User.Read"]),
        ):
            app = msal.PublicClientApplication(
                config["public_client_id"],
                authority=authority,
                instance_discovery=False,
                validate_authority=False,
                http_client=transport,
            )
            flow = app.initiate_auth_code_flow(
                scopes, redirect_uri="http://localhost:8123/callback"
            )
            response = transport.get(flow["auth_uri"], timeout=15)
            response.raise_for_status()
            fields = {
                name: html.unescape(
                    re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1]
                )
                for name in ("transaction", "csrf")
            }
            user = json.loads(Path(config["user_file"]).read_text())
            for path, values in (
                ("/login", user),
                ("/consent", {"decision": "approve"}),
                ("/mfa", {"decision": "complete"}),
            ):
                if path == "/mfa":
                    values = mfa_form_values(response.text, values)
                response = transport.post(
                    config["endpoints"]["login"] + path,
                    data={**fields, **values},
                    allow_redirects=False,
                    timeout=15,
                )
                assert response.status_code in (200, 303), "Local browser approval failed"
            callback = dict(parse_qsl(urlsplit(response.headers["location"]).query))
            tokens = app.acquire_token_by_auth_code_flow(flow, callback)
            assert "access_token" in tokens, tokens.get("error")
            result[label] = tokens["access_token"]
        for label, raw in result.items():
            path = Path(config["token_output_dir"]) / (label + "-token.json")
            path.write_text(json.dumps({"access_token": raw, "flow": "AuthorizationCode"}))
    return result


class Credential:
    def __init__(self, raw):
        self.raw = raw

    async def get_token(self, *args, **kwargs):
        return AccessToken(self.raw, int(time.time()) + 3600)


def graph_client(config, raw, http):
    auth = AzureIdentityAuthenticationProvider(
        Credential(raw),
        scopes=["https://graph.microsoft.com/.default"],
        allowed_hosts=[urlsplit(config["endpoints"]["graph"]).hostname],
    )
    adapter = GraphRequestAdapter(
        auth,
        client=GraphClientFactory.create_with_default_middleware(client=http, options=options),
    )
    adapter.base_url = config["endpoints"]["graph"] + "/v1.0"
    return GraphServiceClient(request_adapter=adapter)


async def delta_round(builder, link=None):
    page = await (builder.with_url(link).get() if link else builder.get())
    rows = list(page.value)
    pages = 1
    while page.odata_next_link:
        page = await builder.with_url(page.odata_next_link).get()
        rows.extend(page.value)
        pages += 1
        assert pages < 150
    assert page.odata_delta_link
    return rows, page.odata_delta_link


async def workflow(config, raw, denied):
    ssl_context = ssl.create_default_context(cafile=config["ca_file"])
    async with httpx.AsyncClient(verify=ssl_context, trust_env=False, timeout=30) as http:
        graph = graph_client(config, raw, http)
        query = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
            filter="startsWith(userPrincipalName,'seed')",
            select=["id", "displayName"],
            top=333,
        )
        page = await graph.users.get(RequestConfiguration(query_parameters=query))
        users = list(page.value)
        while page.odata_next_link:
            page = await graph.users.with_url(page.odata_next_link).get()
            users.extend(page.value)
            assert len(users) <= 5000
        assert [row.id for row in users] == config["seed_ids"]
        suffix = secrets.token_hex(6)
        user_id = group_id = None
        try:
            user = await graph.users.post(
                User(
                    account_enabled=True,
                    display_name="SDK analyst",
                    mail_nickname="sdk" + suffix,
                    user_principal_name=f"sdk{suffix}@{config['domain']}",
                    password_profile=PasswordProfile(
                        password=secrets.token_urlsafe(24),
                        force_change_password_next_sign_in=False,
                    ),
                )
            )
            user_id = user.id
            assert (await graph.users.by_user_id(user_id).get()).display_name == "SDK analyst"
            group = await graph.groups.post(
                Group(
                    display_name="SDK security " + suffix,
                    mail_nickname="sdk" + suffix,
                    mail_enabled=False,
                    security_enabled=True,
                    group_types=[],
                )
            )
            group_id = group.id
            initial, link = await delta_round(graph.groups.delta)
            assert any(row.id == group_id for row in initial)
            await graph.users.by_user_id(user_id).patch(User(display_name="SDK updated"))
            assert (await graph.users.by_user_id(user_id).get()).display_name == "SDK updated"
            await graph.groups.by_group_id(group_id).members.ref.post(
                ReferenceCreate(
                    odata_id=config["endpoints"]["graph"] + "/v1.0/directoryObjects/" + user_id,
                )
            )
            members = await graph.groups.by_group_id(group_id).members.get()
            assert [row.id for row in members.value] == [user_id]
            changes, link = await delta_round(graph.groups.delta, link)
            changed = next(row for row in changes if row.id == group_id)
            assert any(str(m["id"]) == user_id for m in changed.additional_data["members@delta"])
            await (
                graph.groups.by_group_id(group_id)
                .members.by_directory_object_id(user_id)
                .ref.delete()
            )
            assert not (await graph.groups.by_group_id(group_id).members.get()).value
            await graph.users.by_user_id(user_id).delete()
            user_id = None
            await graph.groups.by_group_id(group_id).delete()
            changes, link = await delta_round(graph.groups.delta, link)
            removed = next(row for row in changes if row.id == group_id)
            assert removed.additional_data["@removed"]["reason"] == "deleted"
            group_id = None
        finally:
            if group_id:
                await graph.groups.by_group_id(group_id).delete()
            if user_id:
                await graph.users.by_user_id(user_id).delete()
    async with httpx.AsyncClient(verify=ssl_context, trust_env=False) as http:
        restricted = graph_client(config, denied, http)
        try:
            await restricted.users.get()
        except APIError as error:
            assert error.response_status_code == 403
        else:
            raise AssertionError("Missing permission was accepted")
    return {
        "seed_user_count": len(users),
        "seed_first_id": users[0].id,
        **dict.fromkeys(
            ("create", "profile_update", "membership", "delta", "delete", "permission_denied"), True
        ),
    }


async def main(config):
    tokens = acquire(config)
    modes = {}
    for mode in ("application", "delegated"):
        modes[mode] = await workflow(config, tokens[mode], tokens["denied_" + mode])
    report = {"passed": True, "client": "Python MSAL and generated Graph SDK", "modes": modes}
    Path(config["output_file"]).write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/state/client.json"))
    arguments = parser.parse_args()
    asyncio.run(main(json.loads(arguments.config.read_text())))
