"""Enroll the explicit local MFA fixture used by fresh native qualifications."""


def ensure_mobile_method(client, tenant_id, user_id):
    path = f"/_mock/tenants/{tenant_id}/security/users/{user_id}/authenticationMethods"
    response = client.get(path)
    response.raise_for_status()
    body = response.json()
    methods = body.get("value", body.get("items", []))
    if any(
        row.get("@odata.type") == "#microsoft.graph.phoneAuthenticationMethod"
        and row.get("phoneType") == "mobile"
        for row in methods
    ):
        return
    response = client.post(
        path,
        json={
            "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
            "phoneType": "mobile",
            "phoneNumber": "+1 5555550150",
        },
    )
    response.raise_for_status()
    assert response.status_code == 201
