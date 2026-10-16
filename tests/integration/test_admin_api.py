from uuid import uuid4

import httpx
import pytest

from mockazure.admin.app import create_app

pytestmark = pytest.mark.asyncio


async def test_auth_routes_paging_host_and_errors(database, settings, tenant_payload):
    settings.token_file.write_text("A" * 43)
    app = create_app(settings, database)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    headers = {"Authorization": "Bearer " + "A" * 43}
    async with httpx.AsyncClient(transport=transport, base_url="https://localhost:8443") as client:
        assert (await client.get("/_mock/health")).json() == {"status": "ok"}
        assert (await client.get("/_mock/ready")).status_code == 200
        assert (await client.get("/_mock/tenants")).status_code == 401
        assert (
            await client.get("/_mock/tenants", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        created = await client.post("/_mock/tenants", headers=headers, json=tenant_payload)
        assert created.status_code == 201
        assert (
            created.headers["location"]
            == "https://localhost:8443/_mock/tenants/" + created.json()["id"]
        )
        assert (
            await client.get(created.headers["location"], headers=headers)
        ).json() == created.json()
        assert (
            await client.post("/_mock/tenants", headers=headers, json=tenant_payload)
        ).status_code == 409
        assert (
            await client.post(
                "/_mock/tenants", headers=headers, json={**tenant_payload, "password": "secret"}
            )
        ).status_code == 422
        for index in range(2):
            assert (
                await client.post(
                    "/_mock/tenants",
                    headers=headers,
                    json={**tenant_payload, "slug": f"t{index}", "domain": f"t{index}.test"},
                )
            ).status_code == 201
        page = (await client.get("/_mock/tenants?limit=2", headers=headers)).json()
        assert len(page["items"]) == 2 and page["next_after"]
        other = (
            await client.get(
                "/_mock/tenants", headers=headers, params={"limit": 2, "after": page["next_after"]}
            )
        ).json()
        assert len(other["items"]) == 1 and other["next_after"] is None
        assert not {item["id"] for item in page["items"]} & {item["id"] for item in other["items"]}
        assert (await client.get("/_mock/tenants?limit=501", headers=headers)).status_code == 422
        assert (await client.get("/_mock/tenants/not-uuid", headers=headers)).status_code == 422
        assert (await client.get(f"/_mock/tenants/{uuid4()}", headers=headers)).status_code == 404
        assert (await client.get("/_mock/unknown", headers=headers)).status_code == 404
        assert (await client.delete("/_mock/tenants", headers=headers)).status_code == 405
        assert (await client.get("/_mock/capabilities", headers=headers)).json()[
            "provider_operations"
        ] == []
        response = await client.get("/v1.0/users")
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "MockAzureOperationNotImplemented"
        assert (
            await client.get("/_mock/health", headers={"Host": "hostile.invalid"})
        ).status_code == 400


async def test_readiness_outdated_and_database_errors_are_sanitized(settings):
    settings.token_file.write_text("A" * 43)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="https://localhost:8443",
        ) as client:
            assert (await client.get("/_mock/ready")).status_code == 503
            response = await client.get(
                "/_mock/tenants", headers={"Authorization": "Bearer " + "A" * 43}
            )
            assert response.status_code == 503
            for secret in (
                "SELECT",
                "Traceback",
                "A" * 43,
                settings.database_url.get_secret_value(),
            ):
                assert secret not in response.text
