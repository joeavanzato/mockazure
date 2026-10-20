from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import delete, select

from mockazure.evidence.models import ProjectionReceipt
from mockazure.evidence.projectors import project_source
from mockazure.evidence.repository import append_source, load_source, publish_batch
from mockazure.evidence.schemas import SourceInput, SourceRef
from mockazure.hunting.models import HuntingRecord
from mockazure.hunting.projection import project_hunting
from tests.integration.evidence_support import tenant

NOW = datetime(2026, 10, 7, tzinfo=UTC)


def workload(tid):
    return SourceInput(
        ref=SourceRef(tenant_id=tid, kind="workload", id=uuid4()),
        occurred_at=NOW,
        available_at=NOW + timedelta(seconds=5),
        facts={
            "operation": "drive.upload",
            "scope": "site",
            "actor": {"user": {"id": str(uuid4()), "userPrincipalName": "user@local.test"}},
            "before": {},
            "after": {"id": "file-1", "name": "report.docx"},
            "provenance": "native",
            "mapping_version": "business-evidence-v1",
        },
    )


async def test_native_reprojection_is_idempotent_and_reset_does_not_republish(database):
    async with database.sessions.begin() as session:
        tid = await tenant(session)
        value = workload(tid)
        await append_source(session, value)
        source = await load_source(session, value.ref)
        projected = project_hunting(source)
        assert [p.table for p in projected] == ["CloudAppEvents"]
        await publish_batch(session, project_source(source))
        records = list(await session.scalars(select(HuntingRecord)))
        assert len(records) == 1 and records[0].provenance["kind"] == "native"
        assert records[0].payload["ActionType"] == "FileUploaded"
        assert records[0].available_at == value.available_at
        assert str(source.generation) in records[0].provenance["source_ref"]
        assert (await publish_batch(session, project_source(source))).already_applied
        assert len(list(await session.scalars(select(HuntingRecord)))) == 1
        await session.execute(delete(HuntingRecord).where(HuntingRecord.tenant_id == tid))
        assert (await publish_batch(session, project_source(source))).already_applied
        assert list(await session.scalars(select(HuntingRecord))) == []
        assert await session.scalar(select(ProjectionReceipt)) is not None


async def test_graph_read_and_historical_import_do_not_invent_sensor_events(database):
    from mockazure.evidence.schemas import SourceEnvelope

    async with database.sessions.begin() as session:
        tid = await tenant(session)
    for kind in ("historical", "authentication", "directory"):
        source = SourceEnvelope(
            ref=SourceRef(tenant_id=tid, kind=kind, id=uuid4()),
            occurred_at=NOW,
            available_at=NOW,
            source_revision=1,
            generation=uuid4(),
            facts={},
        )
        assert project_hunting(source) == []


def test_mail_send_never_invents_url_clicks_or_malware_verdicts():
    from mockazure.evidence.schemas import SourceEnvelope

    user = str(uuid4())
    source = SourceEnvelope(
        ref=SourceRef(tenant_id=uuid4(), kind="mail", id=uuid4()),
        occurred_at=NOW,
        available_at=NOW,
        source_revision=1,
        generation=uuid4(),
        facts={
            "operation": "Send",
            "actor": {"user": {"id": user, "userPrincipalName": "u@local.test"}},
            "mailbox": {"id": user, "upn": "u@local.test"},
            "before": {},
            "after": {"id": "message-one"},
            "provenance": "native",
        },
    )
    rows = project_hunting(source)
    assert len(rows) == 1 and rows[0].table == "CloudAppEvents"
    assert rows[0].row["ActionType"] == "Send"
    assert "ThreatTypes" not in rows[0].row and "IsClickedThrough" not in rows[0].row


async def test_native_hunting_waits_for_source_availability_and_import_stays_fixture(
    database, settings
):
    from mockazure.evidence.clock import update_settings
    from mockazure.evidence.schemas import EvidenceSettings
    from tests.integration.test_graph_hunting import NOW as QUERY_NOW
    from tests.integration.test_graph_hunting import PATH, hunting_client

    async with hunting_client(database, settings) as (client, tid, app, user, headers):
        async with database.sessions.begin() as session:
            value = workload(tid).model_copy(
                update={
                    "occurred_at": QUERY_NOW,
                    "available_at": QUERY_NOW + timedelta(seconds=5),
                }
            )
            await append_source(session, value)
            source = await load_source(session, value.ref)
            await publish_batch(session, project_source(source))
        query = {"Query": "CloudAppEvents | project ActionType, ReportId"}
        assert (await client.post(PATH, headers=headers, json=query)).json()["results"] == []
        async with database.sessions.begin() as session:
            await update_settings(
                session,
                tid,
                EvidenceSettings(
                    clock_mode="fixed",
                    fixed_time=QUERY_NOW + timedelta(seconds=6),
                ),
            )
        rows = (await client.post(PATH, headers=headers, json=query)).json()["results"]
        assert len(rows) == 1 and rows[0]["ActionType"] == "FileUploaded"
        exported = (
            await client.get(
                f"/_mock/tenants/{tid}/hunting/tables/CloudAppEvents/export",
            )
        ).json()["items"][0]["properties"]["record"]
        imported = await client.post(
            f"/_mock/tenants/{tid}/hunting/tables/CloudAppEvents/import",
            json={"source_ids": ["restored-fixture"], "rows": [exported]},
        )
        assert imported.status_code == 200
        async with database.sessions() as session:
            restored = await session.get(HuntingRecord, (tid, "CloudAppEvents", "restored-fixture"))
            assert restored.provenance["kind"] == "fixture"
            assert len(list(await session.scalars(select(ProjectionReceipt)))) == 1
