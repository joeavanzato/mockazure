import asyncio
import base64
import hashlib
import html
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt

from mockazure.admin.app import create_app
from tests.integration.identity_support import identity_client

PASSWORD = "Local test password 42!"
CALLBACK = "http://localhost:8123/callback"
VERIFIER = "v" * 64


def hidden(response, name):
    return html.unescape(re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1])


async def provision(client, tenant, permissions=None):
    base = f"/_mock/tenants/{tenant}/identity"
    user = (
        await client.post(
            base + "/users",
            json={
                "upn": "reader@contoso.test",
                "display_name": "<script>Reader</script>",
                "password": PASSWORD,
            },
        )
    ).json()
    app = (
        await client.post(
            base + "/applications",
            json={
                "display_name": "<script>Client</script>",
                "client_mode": "public",
                "redirect_uris": [CALLBACK],
                "required_permissions": [
                    {
                        "resource": "graph",
                        "kind": "delegated",
                        "permissions": permissions or ["User.Read"],
                    }
                ],
            },
        )
    ).json()
    return user, app


def params(app, **extra):
    return {
        "client_id": app["client_id"],
        "redirect_uri": CALLBACK,
        "response_type": "code",
        "scope": "openid profile email User.Read",
        "state": "a & b",
        "nonce": "test-nonce",
        "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest())
        .decode()
        .rstrip("="),
        "code_challenge_method": "S256",
        **extra,
    }


async def sign_in(client, tenant, app, *, consent="approve", mfa="skip", **extra):
    response = await client.get(f"/{tenant}/oauth2/v2.0/authorize", params=params(app, **extra))
    assert response.status_code == 200, response.text
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "<script>Client</script>" not in response.text
    fields = {key: hidden(response, key) for key in ("transaction", "csrf")}
    response = await client.post(
        "/login", data={**fields, "username": "reader@contoso.test", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    response = await client.post("/consent", data={**fields, "decision": consent})
    if consent == "approve" and response.status_code == 200:
        response = await client.post("/mfa", data={**fields, "decision": mfa})
    return response


async def test_pkce_browser_and_atomic_redemption(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app = await provision(client, tenant)
        callback = await sign_in(client, tenant, app, mfa="complete")
        assert callback.status_code == 303, callback.text
        values = parse_qs(urlsplit(callback.headers["location"]).query)
        assert values["state"] == ["a & b"]
        token_fields = {
            "grant_type": "authorization_code",
            "client_id": app["client_id"],
            "redirect_uri": CALLBACK,
            "code": values["code"][0],
            "code_verifier": VERIFIER,
        }
        bad = await client.post(
            f"/{tenant}/oauth2/v2.0/token", data={**token_fields, "code_verifier": "z" * 64}
        )
        assert bad.json()["error"] == "invalid_grant"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as replica:
            responses = await asyncio.gather(
                *(
                    target.post(f"/{tenant}/oauth2/v2.0/token", data=token_fields)
                    for target in (client, replica)
                )
            )
        assert sorted(r.status_code for r in responses) == [200, 400]
        tokens = next(r.json() for r in responses if r.status_code == 200)
        assert "refresh_token" not in tokens
        claims = jwt.decode(tokens["id_token"], options={"verify_signature": False})
        assert claims["nonce"] == "test-nonce" and claims["oid"] == user["id"]
        access = jwt.decode(tokens["access_token"], options={"verify_signature": False})
        assert access["scp"] == "User.Read" and "roles" not in access
        me = await client.get(
            "/v1.0/me", headers={"Authorization": "Bearer " + tokens["access_token"]}
        )
        assert me.json()["id"] == user["id"]


async def test_browser_rejects_untrusted_inputs_and_consent(database, settings):
    async with identity_client(database, settings) as (client, tenant, *_):
        user, app = await provision(client, tenant, ["User.Read", "Organization.Read.All"])
        for extra in (
            {"redirect_uri": "https://evil.example/callback"},
            {"code_challenge_method": "plain"},
            {"code_challenge": ""},
        ):
            response = await client.get(
                f"/{tenant}/oauth2/v2.0/authorize", params=params(app, **extra)
            )
            assert response.status_code == 400 and "location" not in response.headers
        response = await client.get(f"/{tenant}/oauth2/v2.0/authorize", params=params(app))
        fields = {key: hidden(response, key) for key in ("transaction", "csrf")}
        bad = await client.post(
            "/login",
            data={
                **fields,
                "csrf": "wrong",
                "username": "reader@contoso.test",
                "password": PASSWORD,
            },
        )
        assert bad.status_code == 400
        denied = await sign_in(client, tenant, app, consent="deny")
        assert parse_qs(urlsplit(denied.headers["location"]).query)["error"] == ["access_denied"]
        admin_scope = await sign_in(client, tenant, app, scope="Organization.Read.All")
        assert admin_scope.status_code == 400
        assert admin_scope.json()["error"] == "consent_required"
        denied_mfa = await sign_in(client, tenant, app, mfa="deny")
        assert parse_qs(urlsplit(denied_mfa.headers["location"]).query)["error"] == [
            "access_denied"
        ]
        disabled = await sign_in(client, tenant, app)
        code = parse_qs(urlsplit(disabled.headers["location"]).query)["code"][0]
        await client.patch(
            f"/_mock/tenants/{tenant}/identity/users/{user['id']}", json={"enabled": False}
        )
        result = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "authorization_code",
                "client_id": app["client_id"],
                "redirect_uri": CALLBACK,
                "code": code,
                "code_verifier": VERIFIER,
            },
        )
        assert result.json()["error"] == "invalid_grant"
