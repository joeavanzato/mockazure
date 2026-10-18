"""Isolated qualification: real MSAL browser/device flows and generated Graph SDK calls."""

import asyncio
import html
import json
import re
import ssl
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import msal
import requests
from azure.core.credentials import AccessToken
from kiota_abstractions.api_error import APIError
from kiota_authentication_azure.azure_identity_authentication_provider import (
    AzureIdentityAuthenticationProvider,
)
from msgraph import GraphServiceClient
from msgraph.graph_request_adapter import GraphRequestAdapter, options
from msgraph_core import GraphClientFactory

try:
    from .mfa_form import mfa_form_values
except ImportError:  # Direct script execution in the isolated client container.
    from mfa_form import mfa_form_values


config = json.loads(Path("/state/client.json").read_text())
user = json.loads(Path(config["user_file"]).read_text())


def approve(transport, url):
    response = transport.get(url, timeout=15)
    response.raise_for_status()
    fields = {
        name: html.unescape(re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1])
        for name in ("transaction", "csrf")
    }
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
        if response.status_code not in (200, 303):
            raise RuntimeError("Local browser flow failed")
    return response


async def graph_probe(raw, flow, expect_denied=False):
    class Credential:
        async def get_token(self, *args, **kwargs):
            return AccessToken(raw, int(time.time()) + 3600)

    auth = AzureIdentityAuthenticationProvider(
        Credential(),
        scopes=["User.Read"],
        allowed_hosts=[urlsplit(config["endpoints"]["graph"]).hostname],
    )
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False
    ) as http:
        adapter = GraphRequestAdapter(
            auth,
            client=GraphClientFactory.create_with_default_middleware(client=http, options=options),
        )
        adapter.base_url = config["endpoints"]["graph"] + "/v1.0"
        graph = GraphServiceClient(request_adapter=adapter)
        try:
            organizations = await graph.organization.get()
            if expect_denied:
                raise RuntimeError("Missing-permission request unexpectedly succeeded")
            assert [row.id for row in organizations.value] == [config["tenant_id"]]
            me = await graph.me.get()
            assert me.id == config["user_id"]
            return {
                "flow": flow,
                "passed": True,
                "organization_id": config["tenant_id"],
                "user_id": me.id,
            }
        except APIError as error:
            if expect_denied and error.response_status_code == 403:
                return {"flow": flow, "passed": True, "denied_status": 403}
            raise


async def main():
    results = []
    with requests.Session() as transport:
        transport.verify, transport.trust_env = config["ca_file"], False
        sdk = msal.PublicClientApplication(
            config["public_client_id"],
            authority=config["endpoints"]["login"] + "/common",
            validate_authority=False,
            instance_discovery=False,
            http_client=transport,
        )
        flow = sdk.initiate_auth_code_flow(
            ["User.Read", "Organization.Read.All"], redirect_uri="http://localhost:8123/callback"
        )
        response = approve(transport, flow["auth_uri"])
        callback = dict(parse_qsl(urlsplit(response.headers["location"]).query))
        tokens = sdk.acquire_token_by_auth_code_flow(flow, callback)
        assert "access_token" in tokens, tokens.get("error")
        results.append(await graph_probe(tokens["access_token"], "authorization_code"))
        Path("/state/authorization-code-token.json").write_text(
            json.dumps({"access_token": tokens["access_token"], "flow": "AuthorizationCode"})
        )
        device = sdk.initiate_device_flow(["User.Read", "Organization.Read.All"])
        assert "device_code" in device, device.get("error")
        # Use the internal alias; the advertised verification URI is for host browsers.
        approve(
            transport, config["endpoints"]["login"] + "/device?user_code=" + device["user_code"]
        )
        tokens = sdk.acquire_token_by_device_flow(device)
        assert "access_token" in tokens, tokens.get("error")
        results.append(await graph_probe(tokens["access_token"], "device_code"))
        Path("/state/device-token.json").write_text(
            json.dumps({"access_token": tokens["access_token"], "flow": "Device"})
        )
        renewed = sdk.acquire_token_silent_with_error(
            ["User.Read", "Organization.Read.All"], sdk.get_accounts()[0], force_refresh=True
        )
        assert renewed and "access_token" in renewed
        results.append(await graph_probe(renewed["access_token"], "refresh_token"))
        denied = msal.ConfidentialClientApplication(
            config["denied_client_id"],
            client_credential=json.loads(Path(config["negative_secret_file"]).read_text())[
                "secret"
            ],
            authority=config["endpoints"]["login"] + "/" + config["tenant_id"],
            validate_authority=False,
            instance_discovery=False,
            http_client=transport,
        )
        tokens = denied.acquire_token_for_client(["https://graph.microsoft.com/.default"])
        results.append(await graph_probe(tokens["access_token"], "missing_permission", True))
        invalid = msal.ConfidentialClientApplication(
            config["client_id"],
            client_credential="invalid-local-credential",
            authority=config["endpoints"]["login"] + "/" + config["tenant_id"],
            validate_authority=False,
            instance_discovery=False,
            http_client=transport,
        )
        assert invalid.acquire_token_for_client(["https://graph.microsoft.com/.default"])[
            "error"
        ] == ("invalid_client")
    report = {
        "schema_version": 1,
        "client": "Python MSAL/Graph SDK",
        "passed": True,
        "native_acquisition": True,
        "invalid_credential_rejected": True,
        "probes": results,
    }
    Path("/state/delegated.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


asyncio.run(main())
