import httpx

from mockazure.admin.app import create_app
from tests.integration.test_population_adapters import context, user


async def test_browser_preview_apply_requires_receipt_and_current_version(database, settings):
    ctx = await context(database, settings)
    app = create_app(settings, database)
    headers = {"Authorization": "Bearer " + "A" * 43}
    base = f"/_mock/tenants/{ctx.tenant_id}/resources/security.alerts_v2"
    mutation = {"operation": "import", "logical_key": "a", "payload": {"id": "a", "title": "A"}}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://localhost:8443", headers=headers
    ) as client:
        assert (await client.get("/_mock/resources/catalog")).status_code == 200
        preview = await client.post(base + "/preview", json=mutation)
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data["effect"] == "create"
        assert (
            await client.post(
                base + "/apply", json={"receipt": data["receipt"], "mutation": data["mutation"]}
            )
        ).status_code == 200
        assert (
            await client.post(
                base + "/apply", json={"receipt": data["receipt"], "mutation": data["mutation"]}
            )
        ).status_code == 409
        detail = await client.get(base, params={"identifier": "a"})
        assert detail.json()["items"][0]["properties"]["title"] == "A"
        changed = {**mutation, "payload": {"id": "a", "title": "Changed"}}
        preview = (await client.post(base + "/preview", json=changed)).json()
        assert preview["effect"] == "replace-record"
        assert (
            await client.post(
                base + "/apply",
                json={"receipt": preview["receipt"], "mutation": preview["mutation"]},
            )
        ).status_code == 409
        response = await client.post(
            base + "/apply",
            json={
                "receipt": preview["receipt"],
                "mutation": preview["mutation"],
                "allow_replace": True,
            },
        )
        assert response.status_code == 200, response.text


async def test_browser_auth_payload_contract_and_query_limits(database, settings):
    ctx = await context(database, settings)
    app = create_app(settings, database)
    base = f"/_mock/tenants/{ctx.tenant_id}/resources/directory.users"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="https://localhost:8443"
    ) as client:
        assert (await client.get(base)).status_code == 401
        client.headers["Authorization"] = "Bearer " + "A" * 43
        assert (await client.get(base, params={"limit": 501})).status_code == 422
        value = user(1).model_dump(mode="json")
        value["payload"]["inventedGraphField"] = 1
        assert (await client.post(base + "/preview", json=value)).status_code == 422
        assert (
            await client.post(base + "/apply", json={"mutation": user(2).model_dump(mode="json")})
        ).status_code == 422


async def test_large_browse_does_not_call_export(database, settings, monkeypatch):
    from mockazure.directory import datasets
    from mockazure.population.catalog import get_adapter
    from mockazure.population.types import ResourceQuery

    def forbidden(*args, **kwargs):
        raise AssertionError("Browser must not export entire directories")

    monkeypatch.setattr(datasets, "export_directory", forbidden)
    ctx = await context(database, settings)
    adapter = get_adapter("directory.users")
    await adapter.apply(ctx, [user(i) for i in range(25)])
    page = await adapter.browse(ctx, ResourceQuery(limit=3))
    assert len(page.items) == 3 and page.continuation
