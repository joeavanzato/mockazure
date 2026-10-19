from mockazure.console.sessions import set_password
from tests.integration.test_console_auth import ORIGIN, PASSWORD, browser


async def test_inspector_is_opt_in_and_uses_safe_catalog_projections(database, settings):
    await set_password(database, PASSWORD)
    async with browser(database, settings) as client:
        await client.post(
            "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
        )
        assert (await client.get("/_mock/console/inspector")).status_code == 404
        settings.console_inspector_enabled = True
        catalog = await client.get("/_mock/console/inspector")
        assert catalog.status_code == 200
        keys = {row["key"] for row in catalog.json()["tables"]}
        assert "directory.users" in keys
        assert not any("credential" in key or "session" in key for key in keys)
        for table in ("identity_credentials", "console_sessions", "tenants;DROP TABLE tenants"):
            assert (
                await client.get("/_mock/console/inspector", params={"table_key": table})
            ).status_code == 422
        assert (
            await client.get("/_mock/console/inspector", params={"table_key": "directory.users"})
        ).status_code == 422


async def test_inspector_denies_nested_secret_columns(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from mockazure.console.inspector import inspect_table
    from mockazure.population.types import ResourceItem, ResourcePage

    class Adapter:
        async def browse(self, ctx, query):
            return ResourcePage(
                items=[
                    ResourceItem(
                        id="user",
                        version="1",
                        properties={
                            "id": "user",
                            "displayName": {"password": "secret"},
                            "admin_token": "secret",
                            "userPrincipalName": "safe@example.test",
                            "passwordProfile": {"password": "secret"},
                        },
                    )
                ]
            )

    monkeypatch.setattr("mockazure.console.inspector.get_adapter", lambda key: Adapter())
    tenant = uuid4()
    result = await inspect_table(
        SimpleNamespace(tenant_id=tenant), "directory.users", tenant, 50, None
    )
    assert result.items[0].properties == {"id": "user", "userPrincipalName": "safe@example.test"}
    assert "secret" not in result.model_dump_json()
