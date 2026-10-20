import pytest
from sqlalchemy import func, select

from tests.integration.evidence_support import tenant


async def test_conflicting_source_replay_is_atomic(database):
    from mockazure.hunting.models import HuntingRecord
    from mockazure.hunting.repository import ingest_rows
    from mockazure.hunting.schemas import HuntingProvenance

    provenance = HuntingProvenance(kind="fixture", source_ref="test", generator_version="1")
    row = {"Timestamp": "2026-10-07T00:00:00Z", "DeviceId": "device-one"}
    async with database.sessions.begin() as session:
        tid = await tenant(session)
        assert await ingest_rows(session, tid, "DeviceInfo", [row], ["same"], provenance) == 1
        assert await ingest_rows(session, tid, "DeviceInfo", [row], ["same"], provenance) == 0
        with pytest.raises(ValueError):
            await ingest_rows(
                session,
                tid,
                "DeviceInfo",
                [row, row | {"DeviceId": "other"}],
                ["new", "same"],
                provenance,
            )
        assert await session.scalar(select(func.count()).select_from(HuntingRecord)) == 1


async def test_management_cannot_claim_native_provenance_and_browse_is_scoped(database, settings):
    from mockazure.hunting.repository import browse_rows
    from mockazure.population.catalog import get_adapter
    from mockazure.population.types import AdapterContext, Mutation, ResourceQuery

    async with database.sessions.begin() as session:
        tid, foreign = await tenant(session), await tenant(session, "foreign")
    ctx = AdapterContext(database, settings, tid)
    adapter = get_adapter("hunting.DeviceInfo")
    payload = {"id": "sample", "record": {"Timestamp": "2026-10-07T00:00:00Z", "DeviceId": "safe"}}
    mutation = Mutation(operation="import", logical_key="sample", payload=payload)
    with pytest.raises(ValueError):
        adapter.validate(
            mutation.model_copy(update={"payload": payload | {"provenance": {"kind": "native"}}})
        )
    result = await adapter.apply(ctx, [mutation])
    assert result.items[0].properties["provenance"]["kind"] == "fixture"
    async with database.sessions() as session:
        page = await browse_rows(session, foreign, "DeviceInfo", ResourceQuery())
        assert page.items == []
    assert (await adapter.apply(ctx, [mutation])).effects["unchanged"] == 1


async def test_retention_and_export_do_not_cross_tenants(database):
    from mockazure.hunting.repository import export_rows, ingest_rows, prune_rows
    from mockazure.hunting.schemas import HuntingProvenance
    from mockazure.monitor.tables import instant

    async with database.sessions.begin() as session:
        tid, foreign = await tenant(session), await tenant(session, "foreign")
        row = {"Timestamp": "2026-10-07T00:00:00Z"}
        origin = HuntingProvenance(kind="fixture", source_ref="test", generator_version="1")
        for value in (tid, foreign):
            await ingest_rows(session, value, "DeviceInfo", [row], ["same"], origin)
        snapshot = await export_rows(session, tid, "DeviceInfo", limit=1)
        assert snapshot[0]["record"] == row
        assert await prune_rows(session, tid, instant("2026-10-08T00:00:00Z")) == 1
        assert await export_rows(session, tid, "DeviceInfo", limit=1) == []
        assert len(await export_rows(session, foreign, "DeviceInfo", limit=1)) == 1


async def test_hunting_management_import_export_reset_requires_local_auth(database, settings):
    from tests.integration.identity_support import identity_client

    async with identity_client(database, settings) as (client, tid, *_):
        base = f"/_mock/tenants/{tid}/hunting"
        assert (
            await client.get(base + "/tables", headers={"Authorization": ""})
        ).status_code == 401
        assert len((await client.get(base + "/tables")).json()["tables"]) == 20
        row = {"Timestamp": "2026-10-07T00:00:00Z", "DeviceId": "local"}
        path = base + "/tables/DeviceInfo"
        result = await client.post(path + "/import", json={"rows": [row], "source_ids": ["one"]})
        assert result.status_code == 200 and result.json()["created"] == 1
        forged = await client.post(
            path + "/import",
            json={
                "rows": [row],
                "source_ids": ["two"],
                "provenance": {"kind": "native"},
            },
        )
        assert forged.status_code == 422
        exported = (await client.get(path + "/export")).json()
        assert exported["items"][0]["properties"]["record"] == row
        assert (await client.post(base + "/reset", json={"confirm_tenant_id": tid})).json()[
            "removed"
        ] == 1
        assert (await client.get(path + "/export")).json()["items"] == []
