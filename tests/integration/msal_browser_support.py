import re
from html import unescape
from urllib.parse import parse_qsl

import msal
import requests


def msal_browser_probe(base, tenant_id, ca_file, admin_token, restart=None):
    with requests.Session() as transport:
        transport.verify = str(ca_file)
        transport.trust_env = False
        application = transport.post(
            base + f"/_mock/tenants/{tenant_id}/identity/applications",
            headers={"Authorization": "Bearer " + admin_token},
            json={
                "display_name": "MSAL public client",
                "client_mode": "public",
                "redirect_uris": ["http://localhost:8123/callback"],
                "required_permissions": [
                    {"resource": "graph", "kind": "delegated", "permissions": ["User.Read"]}
                ],
            },
            timeout=10,
        ).json()
        headers = {"Authorization": "Bearer " + admin_token}
        users = transport.get(
            base + f"/_mock/tenants/{tenant_id}/identity/users", headers=headers, timeout=10
        ).json()["items"]
        user = next(item for item in users if item["upn"] == "reader@lab.test")
        enrolled = transport.post(
            base + f"/_mock/tenants/{tenant_id}/security/users/{user['id']}/authenticationMethods",
            headers=headers,
            json={
                "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                "phoneType": "mobile",
                "phoneNumber": "+1 5555550191",
            },
            timeout=10,
        )
        assert enrolled.status_code == 201
        sdk = msal.PublicClientApplication(
            application["client_id"],
            authority=base + "/" + tenant_id,
            validate_authority=False,
            instance_discovery=False,
            http_client=transport,
        )
        flow = sdk.initiate_auth_code_flow(
            scopes=["User.Read"], redirect_uri="http://localhost:8123/callback"
        )
        response = transport.get(flow["auth_uri"], timeout=10)
        assert response.status_code == 200
        fields = {
            name: unescape(re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1])
            for name in ("transaction", "csrf")
        }
        for path, data in (
            ("/login", {"username": "reader@lab.test", "password": "local-password-987"}),
            ("/consent", {"decision": "approve"}),
            ("/mfa", {"decision": "complete"}),
        ):
            if path == "/mfa":
                selected = re.search(r'<option value="([^"]+)"', response.text)
                assert selected, "Fresh profile requires a registered eligible factor"
                data = {**data, "method_id": unescape(selected[1])}
            response = transport.post(
                base + path, data={**fields, **data}, allow_redirects=False, timeout=10
            )
            assert response.status_code in (200, 303)
        callback = dict(parse_qsl(response.headers["location"].split("?", 1)[1]))
        result = sdk.acquire_token_by_auth_code_flow(flow, callback)
        assert "access_token" in result, result.get("error")
        assert result["id_token_claims"]["preferred_username"] == "reader@lab.test"
        assert "refresh_token" in result  # MSAL requests offline_access by default.
        me = transport.get(
            base + "/v1.0/me",
            headers={"Authorization": "Bearer " + result["access_token"]},
            timeout=10,
        )
        assert me.status_code == 200 and me.json()["userPrincipalName"] == "reader@lab.test"

        device = sdk.initiate_device_flow(scopes=["User.Read"])
        assert "device_code" in device, device.get("error")
        response = transport.get(
            base + "/device", params={"user_code": device["user_code"]}, timeout=10
        )
        fields = {
            name: unescape(re.search(r'name="' + name + r'" value="([^"]*)"', response.text)[1])
            for name in ("transaction", "csrf")
        }
        for path, data in (
            ("/login", {"username": "reader@lab.test", "password": "local-password-987"}),
            ("/consent", {"decision": "approve"}),
            ("/mfa", {"decision": "complete"}),
        ):
            if path == "/mfa":
                selected = re.search(r'<option value="([^"]+)"', response.text)
                assert selected, "Fresh profile requires a registered eligible factor"
                data = {**data, "method_id": unescape(selected[1])}
            response = transport.post(base + path, data={**fields, **data}, timeout=10)
            assert response.status_code == 200
        if restart:
            restart()
        device_result = sdk.acquire_token_by_device_flow(device)
        assert "access_token" in device_result, device_result.get("error")
        accounts = sdk.get_accounts()
        assert accounts
        renewed = sdk.acquire_token_silent_with_error(
            ["User.Read"], accounts[0], force_refresh=True
        )
        assert renewed and "access_token" in renewed, renewed.get("error") if renewed else None
        sessions = transport.get(
            base + f"/_mock/tenants/{tenant_id}/identity/sessions",
            headers={"Authorization": "Bearer " + admin_token},
            timeout=10,
        ).json()["items"]
        for row in sessions:
            response = transport.delete(
                base + f"/_mock/tenants/{tenant_id}/identity/sessions/" + row["id"],
                headers={"Authorization": "Bearer " + admin_token},
                timeout=10,
            )
            assert response.status_code == 200
        revoked = sdk.acquire_token_silent_with_error(
            ["User.Read"], accounts[0], force_refresh=True
        )
        assert revoked and revoked["error"] == "invalid_grant"
