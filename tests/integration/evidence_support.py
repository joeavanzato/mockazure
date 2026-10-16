from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from mockazure.db.models import Tenant

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


async def tenant(session, slug="evidence"):
    row = Tenant(slug=slug, display_name=slug, domain=f"{slug}.test", licences=[])
    session.add(row)
    await session.flush()
    return row.id


def audit(record_id=None):
    return {
        "id": str(record_id or uuid4()),
        "activityDateTime": NOW.isoformat(),
        "activityDisplayName": "Update user",
        "category": "UserManagement",
        "initiatedBy": {},
        "targetResources": [],
        "result": "success",
    }


def source(tenant_id, payload=None, source_id=None):
    from mockazure.evidence.schemas import SourceInput, SourceRef

    return SourceInput(
        ref=SourceRef(tenant_id=tenant_id, kind="historical", id=source_id or uuid4()),
        occurred_at=NOW,
        available_at=NOW + timedelta(seconds=5),
        facts={"family": "directoryAudit", "payload": payload or audit()},
    )


async def batch(session, ref):
    from mockazure.evidence.repository import load_source
    from mockazure.evidence.schemas import Projection, ProjectionBatch

    envelope = await load_source(session, ref)
    return ProjectionBatch(
        source=ref,
        generation=envelope.generation,
        items=(
            Projection(
                family="directoryAudit",
                id=envelope.facts["payload"]["id"],
                occurred_at=envelope.occurred_at,
                available_at=envelope.available_at,
                payload=envelope.facts["payload"],
                subject_id=None,
                source_ref=ref,
            ),
        ),
        receipt_status="projected",
    )


async def visible_record_ids(session, tenant_id):
    from mockazure.evidence.models import EvidenceVersion

    return list(
        await session.scalars(
            select(EvidenceVersion.record_id).where(EvidenceVersion.tenant_id == tenant_id)
        )
    )
