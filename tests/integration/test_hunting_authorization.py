from uuid import uuid4

from mockazure.hunting.repository import ingest_rows
from mockazure.hunting.schemas import HuntingProvenance
from mockazure.identity.models import DirectoryRole
from tests.integration.evidence_support import tenant
from tests.integration.graph_support import headers_for
from tests.integration.test_graph_hunting import PATH, hunting_client


async def test_all_join_inputs_require_entitlement_before_loading(database, settings):
    async with hunting_client(database, settings) as (client, tid, app, user, headers):
        await client.put(
            f"/_mock/tenants/{tid}/hunting/settings", json={"entitlements": ["endpoint"]}
        )
        result = await client.post(
            PATH,
            headers=headers,
            json={
                "Query": "DeviceInfo | join (EmailEvents"
                " | project DeviceId=NetworkMessageId) on DeviceId",
            },
        )
        assert result.status_code == 403, result.text
        denied = await headers_for(database, settings, tid, app, None, {"AuditLog.Read.All"})
        assert (
            await client.post(PATH, headers=denied, json={"Query": "DeviceInfo"})
        ).status_code == 403
        wrong = await headers_for(
            database,
            settings,
            tid,
            app,
            None,
            {"ThreatHunting.Read.All"},
            audience="https://management.azure.com",
        )
        assert (
            await client.post(PATH, headers=wrong, json={"Query": "DeviceInfo"})
        ).status_code == 401


async def test_foreign_workspace_falls_back_only_to_local_primary(database, settings):
    async with hunting_client(database, settings) as (client, tid, app, user, headers):
        async with database.sessions.begin() as session:
            foreign = await tenant(session, "other-hunter")
            await ingest_rows(
                session,
                foreign,
                "DeviceInfo",
                [
                    {
                        "Timestamp": "2026-10-07T10:00:00Z",
                        "DeviceId": "foreign-only",
                    }
                ],
                ["foreign"],
                HuntingProvenance(kind="fixture", source_ref="test", generator_version="1"),
            )
        result = await client.post(
            PATH,
            headers=headers,
            json={
                "Query": "DeviceInfo | project DeviceId",
                "workspaceId": str(uuid4()),
            },
        )
        assert result.status_code == 200
        assert "foreign-only" not in result.text
        assert len(result.json()["results"]) == 2


async def test_delegated_data_visibility_constrains_every_input(database, settings):
    async with hunting_client(database, settings) as (client, tid, app, user, app_headers):
        delegated = await headers_for(
            database, settings, tid, app, user, {"ThreatHunting.Read.All"}, "delegated"
        )
        query = {"Query": "DeviceInfo | project DeviceId"}
        assert (await client.post(PATH, headers=delegated, json=query)).status_code == 403
        async with database.sessions.begin() as session:
            session.add(DirectoryRole(tenant_id=tid, user_id=user, role="Security Reader"))
        scoped = await client.put(
            f"/_mock/tenants/{tid}/hunting/visibility/{user}",
            json={
                "view_data": True,
                "tables": ["DeviceInfo", "DeviceEvents"],
                "device_ids": ["allowed"],
            },
        )
        assert scoped.status_code == 200, scoped.text
        result = await client.post(PATH, headers=delegated, json=query)
        assert result.status_code == 200 and result.json()["results"] == [{"DeviceId": "allowed"}]
        assert (
            len((await client.post(PATH, headers=app_headers, json=query)).json()["results"]) == 2
        )
        rejected = await client.post(
            PATH,
            headers=delegated,
            json={
                "Query": "DeviceInfo | join (IdentityInfo"
                " | project DeviceId=AccountObjectId) on DeviceId",
            },
        )
        assert rejected.status_code == 403


async def test_delete_restore_does_not_restore_hunting_data_authority(database, settings):
    async with hunting_client(database, settings) as (client, tid, app, user, app_headers):
        visibility = f"/_mock/tenants/{tid}/hunting/visibility/{user}"
        assert (
            await client.put(
                visibility,
                json={
                    "view_data": True,
                    "tables": ["DeviceInfo"],
                    "device_ids": None,
                },
            )
        ).status_code == 200
        before = await headers_for(
            database, settings, tid, app, user, {"ThreatHunting.Read.All"}, "delegated"
        )
        assert (
            await client.post(PATH, headers=before, json={"Query": "DeviceInfo"})
        ).status_code == 200
        writer = await headers_for(
            database,
            settings,
            tid,
            app,
            None,
            {"User.ReadWrite.All", "User.EnableDisableAccount.All", "User.Read.All"},
        )
        assert (await client.delete(f"/v1.0/users/{user}", headers=writer)).status_code == 204
        assert (
            await client.post(
                f"/v1.0/directory/deletedItems/{user}/restore",
                headers=writer,
                json={},
            )
        ).status_code == 200
        assert (
            await client.patch(
                f"/v1.0/users/{user}",
                headers=writer,
                json={"accountEnabled": True},
            )
        ).status_code == 204
        after = await headers_for(
            database, settings, tid, app, user, {"ThreatHunting.Read.All"}, "delegated"
        )
        assert (
            await client.post(PATH, headers=after, json={"Query": "DeviceInfo"})
        ).status_code == 403


async def test_ueba_columns_require_explicit_entitlement_even_on_empty_table(database, settings):
    async with hunting_client(database, settings) as (client, tid, app, user, headers):
        query = {"Query": "IdentityInfo | project BlastRadius"}
        assert (await client.post(PATH, headers=headers, json=query)).status_code == 400
        assert (
            await client.put(
                f"/_mock/tenants/{tid}/hunting/settings",
                json={"entitlements": ["identity", "sentinel-ueba"]},
            )
        ).status_code == 200
        result = await client.post(PATH, headers=headers, json=query)
        assert result.status_code == 200, result.text
        assert result.json()["results"] == []


async def test_visibility_grant_cannot_race_deletion_and_survive_restore(
    database, settings, monkeypatch
):
    import asyncio

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.dml import Delete

    from mockazure.hunting.access_models import HuntingVisibility

    cleanup_done, finish_delete = asyncio.Event(), asyncio.Event()
    execute = AsyncSession.execute

    async def pause_after_cleanup(session, statement, *args, **kwargs):
        result = await execute(session, statement, *args, **kwargs)
        if isinstance(statement, Delete) and statement.table.name == "hunting_visibility":
            cleanup_done.set()
            await finish_delete.wait()
        return result

    async with hunting_client(database, settings) as (client, tid, app, user, headers):
        writer = await headers_for(
            database,
            settings,
            tid,
            app,
            None,
            {"User.ReadWrite.All", "User.EnableDisableAccount.All", "User.Read.All"},
        )
        monkeypatch.setattr(AsyncSession, "execute", pause_after_cleanup)
        deletion = asyncio.create_task(client.delete(f"/v1.0/users/{user}", headers=writer))
        await asyncio.wait_for(cleanup_done.wait(), 10)
        grant = asyncio.create_task(
            client.put(
                f"/_mock/tenants/{tid}/hunting/visibility/{user}",
                json={"view_data": True, "tables": ["DeviceInfo"], "device_ids": None},
            )
        )
        try:
            # The faulty implementation commits here while the deleting transaction
            # still exposes a live user. The corrected route waits for that transaction.
            await asyncio.wait_for(asyncio.shield(grant), 1)
        except TimeoutError:
            pass
        finally:
            finish_delete.set()
        removed, granted = await asyncio.wait_for(asyncio.gather(deletion, grant), 10)
        assert removed.status_code == 204, removed.text
        assert (
            await client.post(
                f"/v1.0/directory/deletedItems/{user}/restore", headers=writer, json={}
            )
        ).status_code == 200
        assert (
            await client.patch(f"/v1.0/users/{user}", headers=writer, json={"accountEnabled": True})
        ).status_code == 204
        async with database.sessions() as session:
            assert await session.get(HuntingVisibility, (tid, user)) is None
        assert granted.status_code == 422
        delegated = await headers_for(
            database, settings, tid, app, user, {"ThreatHunting.Read.All"}, "delegated"
        )
        assert (
            await client.post(PATH, headers=delegated, json={"Query": "DeviceInfo"})
        ).status_code == 403
