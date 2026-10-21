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
    from mockazure.identity.keys import initialize_identity

    await context(database, settings)
    await initialize_identity(settings, database)
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


async def test_console_preview_is_bound_to_its_administration_session(database, settings):
    from mockazure.console.sessions import set_password
    from tests.integration.test_console_auth import ORIGIN, PASSWORD, browser

    ctx = await context(database, settings)
    await set_password(database, PASSWORD)
    async with browser(database, settings) as first, browser(database, settings) as second:
        headers = []
        for client in (first, second):
            login = await client.post(
                "/_mock/console/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
            )
            assert login.status_code == 200
            headers.append({"Origin": ORIGIN, "X-MockAzure-CSRF": login.json()["csrf_token"]})
        base = f"/_mock/tenants/{ctx.tenant_id}/resources/security.alerts_v2"
        response = await first.post(
            base + "/preview",
            headers=headers[0],
            json={
                "operation": "import",
                "logical_key": "session-test",
                "payload": {"id": "session-test", "title": "Bound preview"},
            },
        )
        assert response.status_code == 200, response.text
        prepared = response.json()
        assert any(c["field"] == "title" and c["kind"] == "added" for c in prepared["changes"])
        body = {"mutation": prepared["mutation"], "receipt": prepared["receipt"]}
        rejected = await second.post(base + "/apply", headers=headers[1], json=body)
        assert (
            rejected.status_code == 409
            and rejected.json()["error"]["code"] == "PreviewContextChanged"
        )
        assert (await first.post(base + "/apply", headers=headers[0], json=body)).status_code == 200


async def test_run_routes_reject_foreign_selected_tenant(database, settings):
    from datetime import UTC, datetime
    from uuid import uuid4

    from mockazure.identity.keys import initialize_identity
    from mockazure.population.planner import preview
    from mockazure.population.repository import submit_run
    from tests.integration.test_population_runs import request_for

    ctx = await context(database, settings)
    await initialize_identity(settings, database)
    prepared = await preview(database, request_for(ctx.tenant_id), datetime.now(UTC))
    run = await submit_run(database, prepared.id, prepared.digest, datetime.now(UTC))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(create_app(settings, database)),
        base_url="https://localhost:8443",
        headers={"Authorization": "Bearer " + "A" * 43},
    ) as client:
        for suffix in ("", "/manifest", "/steps", "/ground-truth", "/cancel", "/resume"):
            method = "POST" if suffix in {"/cancel", "/resume"} else "GET"
            response = await client.request(
                method,
                f"/_mock/population/runs/{run.id}{suffix}",
                params={"tenant_id": str(uuid4())},
            )
            assert response.status_code == 404, response.text
        own = await client.get(
            f"/_mock/population/runs/{run.id}", params={"tenant_id": str(ctx.tenant_id)}
        )
        assert own.status_code == 200 and own.json()["state"] == "pending"
