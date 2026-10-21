import httpx

from mockazure.admin.app import create_app
from tests.integration.test_population_adapters import context


async def test_workbench_catalog_exposes_curated_reads_and_existing_operations(database, settings):
    await context(database, settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(settings, database)),
        base_url="https://localhost:8443",
        headers={"Authorization": "Bearer " + "A" * 43},
    ) as client:
        response = await client.get("/_mock/console/workbench/catalog")
        assert response.status_code == 200, response.text
        families = {f["key"]: f for f in response.json()["families"]}
        user = families["directory.users"]
        assert {"read-list", "read-item", "update", "import"} <= {
            op["key"] for op in user["operations"]
        }
        assert families["storage.blobs"]["operations"]
        read = next(op for op in user["operations"] if op["key"] == "read-item")
        assert read["method"] == "GET" and read["identity_required"]


async def test_workbench_public_read_write_denial_preview_binding_and_conflict(
    database, settings, monkeypatch
):
    from uuid import UUID

    from mockazure.population.actions import PublicActions
    from mockazure.population.catalog import get_adapter
    from mockazure.population.identity import resolve_identity
    from mockazure.population.transport import LocalTransport
    from mockazure.population.types import AdapterContext, Mutation
    from tests.integration.graph_support import graph_client

    async with graph_client(database, settings) as (client, tenant, _, owner):
        ctx = AdapterContext(database, settings, UUID(str(tenant)))
        for name, perms in [
            ("reader", ["User.Read.All"]),
            ("writer", ["User.Read.All", "User.ReadWrite.All"]),
        ]:
            await get_adapter("identity.clients").apply(
                ctx,
                [
                    Mutation(
                        operation="provision",
                        logical_key=name,
                        payload={
                            "id": name,
                            "display_name": name,
                            "permissions": [
                                {"resource": "graph", "kind": "application", "permissions": perms}
                            ],
                        },
                    )
                ],
            )

        async def transport(self, context, identity):
            return LocalTransport(context, await resolve_identity(context, identity), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)
        headers = {"Authorization": "Bearer " + "A" * 43}
        base = f"/_mock/tenants/{tenant}/resources/directory.users"
        value = {
            "operation": "update",
            "logical_key": "update-test",
            "identity_ref": "reader",
            "payload": {
                "variables": {"id": str(owner)},
                "body": {"displayName": "Reviewed update"},
            },
        }
        read = await client.post(
            f"/_mock/console/tenants/{tenant}/resources/directory.users/read",
            headers=headers,
            json={
                "operation": "read-item",
                "identity_ref": "reader",
                "payload": {"variables": {"id": str(owner)}},
            },
        )
        assert read.status_code == 200 and read.json()["status"] == 200, read.text
        preview = await client.post(base + "/preview", headers=headers, json=value)
        assert preview.status_code == 200, preview.text
        data = preview.json()
        assert data["effect"] == "update" and data["changes"]
        rejected = await client.post(
            base + "/apply",
            headers=headers,
            json={"mutation": data["mutation"], "receipt": data["receipt"]},
        )
        assert rejected.status_code == 403, rejected.text
        value["identity_ref"] = "writer"
        data = (await client.post(base + "/preview", headers=headers, json=value)).json()
        tampered = {**data["mutation"], "identity_ref": "reader"}
        assert (
            await client.post(
                base + "/apply",
                headers=headers,
                json={"mutation": tampered, "receipt": data["receipt"]},
            )
        ).status_code == 409
        result = await client.post(
            base + "/apply",
            headers=headers,
            json={"mutation": data["mutation"], "receipt": data["receipt"]},
        )
        assert result.status_code == 200, result.text
        assert result.json()["items"][0]["properties"]["status_code"] == 204
        again = await client.post(
            base + "/apply",
            headers=headers,
            json={"mutation": data["mutation"], "receipt": data["receipt"]},
        )
        assert again.status_code == 409, again.text
        # A caller cannot use a curated read as an arbitrary URL proxy.
        assert (
            await client.post(
                f"/_mock/console/tenants/{tenant}/resources/directory.users/read",
                headers=headers,
                json={
                    "operation": "read-item",
                    "identity_ref": "reader",
                    "payload": {"variables": {"id": "../_mock"}},
                },
            )
        ).status_code == 422


async def test_fixture_replacement_removes_omitted_properties(database, settings):
    ctx = await context(database, settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(settings, database)),
        base_url="https://localhost:8443",
        headers={"Authorization": "Bearer " + "A" * 43},
    ) as client:
        base = f"/_mock/tenants/{ctx.tenant_id}/resources/security.alerts_v2"

        async def preview(payload):
            result = await client.post(
                base + "/preview",
                json={"operation": "import", "logical_key": "replacement", "payload": payload},
            )
            assert result.status_code == 200, result.text
            return result.json()

        first = await preview({"id": "replacement", "title": "Keep", "description": "Remove me"})
        assert (
            await client.post(
                base + "/apply", json={"mutation": first["mutation"], "receipt": first["receipt"]}
            )
        ).status_code == 200
        second = await preview({"id": "replacement", "title": "Keep"})
        assert any(
            c["field"] == "description" and c["kind"] == "removed" for c in second["changes"]
        )
        assert (
            await client.post(
                base + "/apply",
                json={
                    "mutation": second["mutation"],
                    "receipt": second["receipt"],
                    "allow_replace": True,
                },
            )
        ).status_code == 200
        item = (await client.get(base, params={"identifier": "replacement"})).json()["items"][0]
        assert "description" not in item["properties"]


async def test_profile_console_defaults_persist_with_saved_revisions(database, settings):
    await context(database, settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(settings, database)),
        base_url="https://localhost:8443",
        headers={"Authorization": "Bearer " + "A" * 43},
    ) as client:
        bundle = (await client.get("/_mock/profiles/builtin")).json()
        defaults = {
            "datasetNamespace": "review-lab",
            "createTenant": True,
            "tenantSlug": "review-lab",
            "tenantDomain": "review-lab.test",
            "displayName": "Review lab",
            "activityEnabled": True,
            "activityStep": "calendar",
            "actionCount": 4,
            "pacingMs": 20,
        }
        bundle["documents"]["developer-lab"]["consoleDefaults"] = defaults
        result = await client.post("/_mock/profiles", json={"documents": bundle["documents"]})
        assert result.status_code == 201, result.text
        saved = (await client.get("/_mock/profiles/" + result.json()["id"])).json()
        assert saved["documents"]["developer-lab"]["consoleDefaults"] == defaults
