"""Unmodified generated Graph SDK writes and delegated reset over local HTTPS."""

import asyncio
import html
import json
import re
import ssl
from importlib.metadata import version
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import httpx
import msal
import requests
from graph_directory import graph_client
from graph_security import client_token
from msgraph.generated.models.authentication_phone_type import AuthenticationPhoneType
from msgraph.generated.models.email_authentication_method import EmailAuthenticationMethod
from msgraph.generated.models.phone_authentication_method import PhoneAuthenticationMethod
from msgraph.generated.users.item.authentication.methods.item.reset_password.reset_password_post_request_body import (  # noqa: E501
    ResetPasswordPostRequestBody,
)

try:
    from .mfa_form import mfa_form_values
except ImportError:  # Direct script execution in the isolated client container.
    from mfa_form import mfa_form_values


PASSWORD_ID = "28c10230-6103-485e-b985-444c60001490"


def delegated(config):
    with requests.Session() as transport:
        transport.verify, transport.trust_env = config["ca_file"], False
        app = msal.PublicClientApplication(
            config["public_client_id"],
            authority=config["endpoints"]["login"] + "/" + config["tenant_id"],
            instance_discovery=False,
            validate_authority=False,
            http_client=transport,
        )
        flow = app.initiate_auth_code_flow(
            config.get("delegated_scopes", ["UserAuthenticationMethod.ReadWrite.All"]),
            redirect_uri="http://localhost:8123/callback",
        )
        response = transport.get(flow["auth_uri"], timeout=30)
        response.raise_for_status()
        fields = {
            name: html.unescape(
                re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1]
            )
            for name in ("transaction", "csrf")
        }
        user = json.loads(Path("/state/admin-user.json").read_text(encoding="utf-8"))
        for path, body in (
            ("/login", user),
            ("/consent", {"decision": "approve"}),
            ("/mfa", {"decision": "complete"}),
        ):
            if path == "/mfa":
                body = mfa_form_values(response.text, body)
            response = transport.post(
                config["endpoints"]["login"] + path,
                data={**fields, **body},
                allow_redirects=False,
                timeout=30,
            )
            assert response.status_code in (200, 303), "Local delegated approval failed"
        callback = dict(parse_qsl(urlsplit(response.headers["location"]).query))
        token = app.acquire_token_by_auth_code_flow(flow, callback)
        assert "access_token" in token, token.get("error")
        Path("/state/delegated-token.json").write_text(
            json.dumps({"access_token": token["access_token"], "flow": "AuthorizationCode"}),
            encoding="utf-8",
        )
        return token["access_token"]


async def main():
    config = json.loads(Path("/state/client.json").read_text(encoding="utf-8"))
    token = client_token(
        config,
        config["client_id"],
        json.loads(Path(config["secret_file"]).read_text(encoding="utf-8"))["secret"],
    )
    admin = delegated(config)
    locations = []

    async def capture(response):
        if response.request.url.path.endswith("/resetPassword") and response.status_code == 202:
            locations.append(response.headers["location"])

    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]),
        trust_env=False,
        timeout=30,
        event_hooks={"response": [capture]},
    ) as http:
        graph = graph_client(config, token["access_token"], http)
        methods = graph.users.by_user_id(config["python_user_id"]).authentication
        phone = await methods.phone_methods.post(
            PhoneAuthenticationMethod(
                phone_number="+1 5555550123", phone_type=AuthenticationPhoneType.Mobile
            )
        )
        assert phone.id == "3179e48a-750b-4051-897c-87b9720928f7"
        item = methods.phone_methods.by_phone_authentication_method_id(phone.id)
        await item.patch(PhoneAuthenticationMethod(phone_number="+1 5555550124"))
        assert (await item.get()).phone_number == "+1 5555550124"
        await item.delete()
        email = await methods.email_methods.post(
            EmailAuthenticationMethod(email_address="recover@example.test")
        )
        assert email.id == "3ddfcfc8-9383-446f-83cc-3ab9be4be18f"
        item = methods.email_methods.by_email_authentication_method_id(email.id)
        await item.patch(EmailAuthenticationMethod(email_address="changed@example.test"))
        assert (await item.get()).email_address == "changed@example.test"
        await item.delete()
        await methods.fido2_methods.by_fido2_authentication_method_id(
            config["python_fido_id"]
        ).delete()
        authenticator = methods.microsoft_authenticator_methods
        await authenticator.by_microsoft_authenticator_authentication_method_id(
            config["python_authenticator_id"]
        ).delete()
        assert len((await methods.methods.get()).value) == 1
        delegated_graph = graph_client(config, admin, http)
        user = delegated_graph.users.by_user_id(config["python_user_id"])
        result = await user.authentication.methods.by_authentication_method_id(
            PASSWORD_ID
        ).reset_password.post(
            ResetPasswordPostRequestBody(new_password="Native temporary password 72!")
        )
        assert result is None and len(locations) == 1
        operation_id = locations[0].rsplit("/", 1)[1]
        for _ in range(60):
            operation = await user.authentication.operations.by_long_running_operation_id(
                operation_id
            ).get()
            if operation.status.value in ("succeeded", "failed"):
                break
            await asyncio.sleep(1)
        assert operation.status.value == "succeeded"
        report = {
            "client": "msgraph-sdk",
            "version": version("msgraph-sdk"),
            "method_mutations": 8,
            "reset_operation": operation_id,
            "reset_status": operation.status.value,
        }
        Path("/state/python-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
