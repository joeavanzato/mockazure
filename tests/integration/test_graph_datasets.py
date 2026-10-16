import json
from uuid import uuid4

from sqlalchemy import select

from mockazure.directory.models import EvidenceOutbox
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_delta import finish
from tests.integration.test_graph_mutations import WRITE, counts
from tests.integration.test_identity_authorization import CALLBACK, hidden, params


def dataset():
    user, group = str(uuid4()), str(uuid4())
    return {
        "schema_version": 1,
        "users": [
            {
                "id": user,
                "displayName": "Imported",
                "userPrincipalName": "imported@contoso.test",
                "mailNickname": "imported",
                "accountEnabled": True,
                "createdDateTime": "2026-10-01T00:00:00Z",
            }
        ],
        "groups": [
            {
                "id": group,
                "displayName": "Imported group",
                "mailNickname": "importedgroup",
                "mailEnabled": False,
                "securityEnabled": True,
                "groupTypes": [],
                "createdDateTime": "2026-10-01T00:00:00Z",
            }
        ],
        "relationships": [{"group_id": group, "object_id": user, "relation": "member"}],
        "provenance": {"source": "test fixture", "seed": 42},
    }


async def test_directory_dataset_roundtrip(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        data = dataset()
        base = f"/_mock/tenants/{tenant}/directory"
        imported = await client.post(base + "/import", json=data)
        assert imported.status_code == 201, imported.text
        assert imported.json()["objects"] == 2 and imported.json()["relationships"] == 1
        exported = await client.get(base + "/export")
        assert exported.status_code == 200, exported.text
        snapshot = exported.json()
        assert snapshot["provenance"] == data["provenance"]
        for word in ("password", "verifier", "private_key", "refresh_token", "credentials"):
            assert word not in exported.text
        assert data["users"][0]["id"] in {u["id"] for u in snapshot["users"]}
        assert snapshot["relationships"] == data["relationships"]
        # Preserve global object IDs by round-tripping into a separate empty owned test database.
        from mockazure.config import Settings
        from mockazure.db.engine import close_database, create_database
        from mockazure.db.migrations import migrate
        from tests.integration.conftest import isolated_database_url
        from tests.integration.identity_support import identity_client

        with isolated_database_url() as url:
            other_settings = Settings(data_dir=settings.data_dir / "roundtrip", database_url=url)
            migrate(url)
            other_db = create_database(other_settings)
            try:
                async with identity_client(other_db, other_settings) as (other, other_tenant, *_):
                    path = f"/_mock/tenants/{other_tenant}/directory"
                    result = await other.post(path + "/import", json=snapshot)
                    assert result.status_code == 201, result.text
                    again = await other.get(path + "/export")
                    assert again.json() == snapshot
                    public_app = (
                        await other.post(
                            f"/_mock/tenants/{other_tenant}/identity/applications",
                            json={
                                "display_name": "Login",
                                "client_mode": "public",
                                "redirect_uris": [CALLBACK],
                                "required_permissions": [
                                    {
                                        "resource": "graph",
                                        "kind": "delegated",
                                        "permissions": ["User.Read"],
                                    }
                                ],
                            },
                        )
                    ).json()
                    login = await other.get(
                        f"/{other_tenant}/oauth2/v2.0/authorize", params=params(public_app)
                    )
                    fields = {k: hidden(login, k) for k in ("transaction", "csrf")}
                    denied = await other.post(
                        "/login",
                        data={
                            **fields,
                            "username": "reader@contoso.test",
                            "password": "local-directory-password",
                        },
                    )
                    assert denied.status_code == 400
            finally:
                await close_database(other_db)
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        changes, _ = await finish(
            client, headers, await client.get("/v1.0/users/delta", headers=headers)
        )
        assert data["users"][0]["id"] in {x["id"] for x in changes}
        async with database.sessions() as session:
            fact = await session.scalar(
                select(EvidenceOutbox.facts).where(
                    EvidenceOutbox.tenant_id == tenant,
                    EvidenceOutbox.facts["operation"].as_string() == "import",
                )
            )
            assert fact["provenance"] == data["provenance"]


async def test_import_atomicity_and_limits(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        path = f"/_mock/tenants/{tenant}/directory/import"
        baseline = await counts(database)
        samples = []
        for mutate in [
            lambda d: d.update(schema_version=2),
            lambda d: d["users"][0].update(password="forbidden"),
            lambda d: d.update(signing_keys=[]),
            lambda d: d["users"].append(d["users"][0]),
            lambda d: d["relationships"][0].update(object_id=str(uuid4())),
            lambda d: d["groups"][0].update(groupTypes=["Unified"]),
            lambda d: d["provenance"].update(secret="forbidden"),
        ]:
            data = dataset()
            mutate(data)
            samples.append(data)
        for data in samples:
            rejected = await client.post(path, json=data)
            assert rejected.status_code in (400, 422), rejected.text
            assert await counts(database) == baseline
        huge = await client.post(
            path,
            headers={"Content-Type": "application/json"},
            content=b"x" * (10 * 1024 * 1024 + 1),
        )
        assert huge.status_code == 413
        assert await counts(database) == baseline
        data = dataset()
        assert (await client.post(path, json=data)).status_code == 201
        committed = await counts(database)
        assert (await client.post(path, json=data)).status_code == 409
        assert await counts(database) == committed
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        assert (await client.post(path, headers=headers, json=dataset())).status_code == 401
        assert (await client.post(f"/_mock/tenants/{tenant}/directory/cleanup")).status_code == 200


async def test_streamed_dataset_uses_fixed_snapshot(database, settings):
    from mockazure.directory.datasets import export_directory
    from mockazure.directory.history import current_revision, lock_history
    from tests.integration.test_graph_query import add_users

    async with graph_client(database, settings) as (client, tenant, app, _):
        rows = await add_users(database, tenant, [f"snapshot{i}" for i in range(501)])
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        async with database.sessions.begin() as session:
            await lock_history(session, tenant)
            revision = await current_revision(session, tenant)
            stream = export_directory(session, tenant, revision)
            chunks = [await anext(stream)]
            target = max(rows, key=lambda x: str(x["id"]))
            assert (
                await client.patch(
                    f"/v1.0/users/{target['id']}",
                    headers=headers,
                    json={"displayName": "after snapshot"},
                )
            ).status_code == 204
            async for chunk in stream:
                chunks.append(chunk)
        decoded = json.loads("".join(chunks))
        assert len(decoded["users"]) == 502
        found = next(x for x in decoded["users"] if x["id"] == str(target["id"]))
        assert found["displayName"] == target["display_name"]
        assert max(map(len, chunks)) < 1024 * 1024


async def test_dataset_object_limit_and_external_owner_boundary(database, settings):
    from mockazure.identity.models import ServicePrincipal
    from tests.integration.test_graph_mutations import group_body

    async with graph_client(database, settings) as (client, tenant, app, _):
        base = f"/_mock/tenants/{tenant}/directory"
        data = dataset()
        template = data["users"][0]
        data["users"] = [
            {**template, "id": str(uuid4()), "userPrincipalName": f"limit{i}@contoso.test"}
            for i in range(10000)
        ]
        data["relationships"] = []
        before = await counts(database)
        response = await client.post(base + "/import", json=data)
        assert response.status_code == 422  # 10,000 users + one group exceeds the combined cap.
        assert await counts(database) == before
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        created = await client.post(
            "/v1.0/groups",
            headers=headers,
            json={
                **group_body(),
                "owners@odata.bind": [f"https://localhost:8443/v1.0/servicePrincipals/{sp}"],
            },
        )
        assert created.status_code == 201, created.text
        exported = await client.get(base + "/export")
        assert exported.status_code == 409
        assert exported.json()["error"]["code"] == "UnsupportedDatasetReference"


async def test_export_disconnect_releases_snapshot_transaction(database, settings):
    import pytest
    from starlette.requests import ClientDisconnect, Request

    from mockazure.admin.directory_routes import export_dataset

    async with graph_client(database, settings) as (client, tenant, _, _):
        scope = {
            "type": "http",
            "app": client._transport.app,
            "method": "GET",
            "path": f"/_mock/tenants/{tenant}/directory/export",
            "headers": [],
            "asgi": {"version": "3.0", "spec_version": "2.4"},
        }
        response = await export_dataset(tenant, Request(scope))

        async def disconnected_send(message):
            raise OSError("client disconnected before response body")

        async def receive():
            return {"type": "http.disconnect"}

        with pytest.raises(ClientDisconnect):
            await response(scope, receive, disconnected_send)
        assert database.engine.sync_engine.pool.checkedout() == 0
