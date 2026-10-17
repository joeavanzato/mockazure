"""Snapshot creation and exact indexed SQL predicates for UAL queries."""

import hashlib
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Text, case, cast, func, or_, select
from sqlalchemy.dialects.postgresql import ARRAY, array

from mockazure.auditlog.authorization import authorize_query, require_services
from mockazure.auditlog.models import AuditQuery
from mockazure.auditlog.schemas import query_input
from mockazure.db.job_model import Job
from mockazure.evidence.models import EvidenceCursor, EvidenceVersion
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.evidence.paging import snapshot_evidence

EXPIRY = timedelta(days=7)
COMMON = [
    "Id",
    "CreationTime",
    "RecordType",
    "Operation",
    "OrganizationId",
    "UserType",
    "UserKey",
    "UserId",
    "Workload",
    "ResultStatus",
    "ObjectId",
    "ClientIP",
    "Version",
    "Scope",
]


def pin_key(identifier):
    return hashlib.sha256(("mockazure-audit-query:" + str(identifier)).encode()).hexdigest()


async def create_query(session, actor, body, now):
    allowed = await authorize_query(session, actor, "create")
    snapshot = await snapshot_evidence(session, actor.tenant_id, now)
    filters = query_input(body, snapshot.evidence_time)
    services = filters.get("serviceFilters", [])
    if not services and allowed is not None:
        services = sorted(allowed)
    require_services(allowed, services)
    filters["serviceFilters"] = services
    identifier = uuid4()
    job = Job(
        id=uuid4(),
        tenant_id=actor.tenant_id,
        kind="audit.query",
        source_key=str(identifier),
        payload={"query_id": str(identifier)},
        available_at=now,
    )
    session.add(job)
    await session.flush()
    row = AuditQuery(
        tenant_id=actor.tenant_id,
        id=identifier,
        job_id=job.id,
        generation=snapshot.generation,
        revision=snapshot.revision,
        evidence_time=snapshot.evidence_time,
        retention_cutoff=snapshot.retention_cutoff,
        created_at=now,
        expires_at=now + EXPIRY,
        filters=filters,
        services=services,
    )
    session.add(row)
    # Existing cleanup already respects these SQL pins under the same EvidenceState lock.
    session.add(
        EvidenceCursor(
            token_hash=pin_key(identifier),
            tenant_id=actor.tenant_id,
            generation=snapshot.generation,
            revision=snapshot.revision,
            evidence_time=snapshot.evidence_time,
            retention_cutoff=snapshot.retention_cutoff,
            expires_at=row.expires_at,
            state={"kind": "audit.query", "query_id": str(identifier)},
        )
    )
    await session.flush()
    return query_view(row, job)


def query_view(row, job):
    return {
        **row.filters,
        "id": str(row.id),
        "status": {
            "pending": "notStarted",
            "running": "running",
            "succeeded": "succeeded",
            "failed": "failed",
        }[job.state],
        "approximateReturnedRecordCount": row.record_count,
        "isRecordCountLimitExceeded": False,
    }


async def get_query(session, actor, identifier, now, operation="get"):
    allowed = await authorize_query(session, actor, operation)
    try:
        key = UUID(identifier)
    except (TypeError, ValueError):
        raise GraphError(404, "Request_ResourceNotFound", "Audit query was not found") from None
    pair = (
        await session.execute(
            select(AuditQuery, Job)
            .join(Job, Job.id == AuditQuery.job_id)
            .where(
                AuditQuery.tenant_id == actor.tenant_id,
                AuditQuery.id == key,
            )
        )
    ).first()
    if pair is None:
        raise GraphError(404, "Request_ResourceNotFound", "Audit query was not found")
    row, job = pair
    require_services(allowed, row.services)
    if row.expires_at <= now:
        raise GraphError(410, "AuditQueryExpired", "Audit query has expired")
    return row, job


def source_predicates(row):
    v = EvidenceVersion
    conditions = [
        v.tenant_id == row.tenant_id,
        v.generation == row.generation,
        v.family == "auditLogRecord",
        v.valid_from <= row.revision,
        or_(v.valid_to.is_(None), v.valid_to > row.revision),
        v.event_time >= row.retention_cutoff,
        v.event_time <= row.evidence_time,
        v.available_at <= row.evidence_time,
        v.event_time >= datetime.fromisoformat(row.filters["filterStartDateTime"]),
        v.event_time <= datetime.fromisoformat(row.filters["filterEndDateTime"]),
    ]
    mapping = {
        "serviceFilters": "service",
        "operationFilters": "operation",
        "recordTypeFilters": "auditLogRecordType",
        "userPrincipalNameFilters": "userPrincipalName",
        "ipAddressFilters": "clientIp",
        "objectIdFilters": "objectId",
    }
    for field, prop in mapping.items():
        values = row.filters.get(field)
        if values:
            conditions.append(v.payload[prop].as_string().in_(values))
    units = row.filters.get("administrativeUnitIdFilters")
    if units:
        conditions.append(v.payload["administrativeUnits"].has_any(cast(array(units), ARRAY(Text))))
    keyword = row.filters.get("keywordFilter")
    if keyword:
        nested = v.payload["auditData"]["dynamicProperties"]
        data = case((func.jsonb_typeof(nested) == "object", nested), else_=v.payload["auditData"])
        content = data.op("-")(cast(array(COMMON), ARRAY(Text)))
        conditions.append(
            func.lower(cast(content, Text)).contains(keyword.lower(), autoescape=True)
        )
    return conditions
