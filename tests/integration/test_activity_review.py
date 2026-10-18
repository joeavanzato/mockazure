from datetime import timedelta

import pytest
from sqlalchemy import select

from mockazure.db.job_model import Job
from tests.integration.graph_support import graph_client
from tests.integration.test_activity_boundaries import record
from tests.integration.test_activity_publication import setup
from tests.integration.test_audit_queries import seed


async def test_cleanup_releases_completed_job_audit_payload(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant)
        admin = f"/_mock/tenants/{tenant}/activity"
        await seed(database, tenant, 1)
        result = await client.post(admin + "/drain", json={"max_rows": 100})
        assert result.status_code == 200 and result.json()["processed"] == 1, result.text
        assert result.json()["jobs"][0]["state"] == "succeeded", result.text
        await client.post(
            f"/_mock/tenants/{tenant}/evidence/clock/advance", json={"seconds": 8 * 86400}
        )
        result = await client.post(admin + "/cleanup", json={"max_rows": 100})
        assert result.status_code == 200 and result.json()["pruned_contents"] == 1, result.text
        async with database.sessions() as session:
            jobs = list(
                await session.scalars(
                    select(Job).where(Job.tenant_id == tenant, Job.kind == "activity.publish")
                )
            )
            retained = [job.payload.get("records") for job in jobs if job.payload.get("records")]
        assert not retained, (
            f"Completed jobs still retain {len(retained)} complete audit payload(s): {retained}"
        )


async def test_distinct_source_kinds_with_same_id_both_reach_feed(database, settings):
    from uuid import UUID, uuid4

    from sqlalchemy import func

    from mockazure.activity.models import Content
    from mockazure.evidence.models import EvidenceObservation
    from mockazure.evidence.projectors import project_source
    from mockazure.evidence.repository import append_source, load_source, publish_batch
    from mockazure.evidence.schemas import SourceInput, SourceRef

    async with graph_client(database, settings) as (client, tenant, app, user):
        now = await setup(client, tenant)
        records = await seed(database, tenant, 1, when=now - timedelta(hours=1))
        shared_source_id = UUID(records[0]["id"])
        ref = SourceRef(tenant_id=tenant, kind="mail", id=shared_source_id)
        async with database.sessions.begin() as session:
            await append_source(
                session,
                SourceInput(
                    ref=ref,
                    occurred_at=now,
                    available_at=now,
                    facts={
                        "operation": "Set-Mailbox",
                        "actor": {
                            "user": {"id": str(user), "userPrincipalName": "actor@example.test"}
                        },
                        "mailbox": {"id": str(uuid4()), "upn": "mailbox@example.test"},
                        "before": {"DisplayName": "Before"},
                        "after": {"DisplayName": "After"},
                        "provenance": "native",
                    },
                ),
            )
            await publish_batch(session, project_source(await load_source(session, ref)))
        result = await client.post(
            f"/_mock/tenants/{tenant}/activity/drain", json={"max_rows": 100}
        )
        assert result.status_code == 200, result.text
        async with database.sessions() as session:
            canonical = await session.scalar(
                select(func.count())
                .select_from(EvidenceObservation)
                .where(
                    EvidenceObservation.tenant_id == tenant,
                    EvidenceObservation.family == "auditLogRecord",
                )
            )
            feed = await session.scalar(
                select(func.count()).select_from(Content).where(Content.tenant_id == tenant)
            )
        assert canonical == 2
        assert feed == canonical, (
            f"{canonical} canonical records but only {feed} feed records; drain={result.text}"
        )


@pytest.mark.parametrize(
    "field,value",
    [
        (field, value)
        for field in ("ClientIP", "ObjectId", "ResultStatus", "Scope")
        for value in ([], {}, False, 123)
    ],
)
async def test_import_rejects_invalid_common_field_types(database, settings, field, value):
    async with graph_client(database, settings) as (client, tenant, app, user):
        row = record(tenant, "Exchange", 1)
        row[field] = value
        result = await client.post(
            f"/_mock/tenants/{tenant}/activity/import", json={"records": [row]}
        )
        assert result.status_code == 422, (
            f"{field}={value!r} accepted: {result.status_code} {result.text}"
        )


async def test_cleanup_compacts_only_terminal_job_payloads(database, settings):
    from datetime import UTC, datetime
    from uuid import uuid4

    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant)
        identifiers = {}
        async with database.sessions.begin() as session:
            for status in ("pending", "running", "failed", "succeeded"):
                identifier = identifiers[status] = uuid4()
                session.add(
                    Job(
                        id=identifier,
                        tenant_id=tenant,
                        kind="activity.publish",
                        state=status,
                        available_at=datetime.now(UTC),
                        payload={"generation": str(uuid4()), "records": [{"Operation": "fixture"}]},
                    )
                )
        response = await client.post(
            f"/_mock/tenants/{tenant}/activity/cleanup", json={"max_rows": 100}
        )
        assert response.status_code == 200
        async with database.sessions() as session:
            for status, identifier in identifiers.items():
                row = await session.get(Job, identifier)
                assert bool(row.payload.get("records")) == (status in {"pending", "running"})
                assert "generation" in row.payload
