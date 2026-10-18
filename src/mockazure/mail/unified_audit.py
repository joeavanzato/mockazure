"""Exchange UAL search over the canonical audit store, with durable session paging."""

import hashlib
import ipaddress
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Text, cast, delete, func, insert, literal, or_, select, tuple_

from mockazure.activity.contracts import RECORD_TYPES, from_graph
from mockazure.auditlog.models import AuditRole
from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.evidence.models import EvidenceVersion
from mockazure.mail.authorization import require_exchange_command
from mockazure.mail.models import MailSettings
from mockazure.mail.unified_audit_models import UnifiedAuditItem, UnifiedAuditSession
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.evidence.paging import snapshot_evidence

SESSION_TTL = timedelta(hours=1)  # Explicit local profile, not a claimed Microsoft lifetime.
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MODES = {"returnlargeset": "ReturnLargeSet", "returnnextpreviewpage": "ReturnNextPreviewPage"}
TYPES = {key.lower(): key for key in RECORD_TYPES if RECORD_TYPES[key] >= 0}
TYPE_IDS = {str(value): key for key, value in RECORD_TYPES.items() if value >= 0}


def invalid(message):
    raise GraphError(400, "InvalidParameter", message)


def timestamp(value):
    try:
        # PowerShell proxies serialize ExDateTime-compatible values as ISO timestamps.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, AttributeError, ValueError, OverflowError):
        invalid("An ISO8601 date or timestamp is required")


def inputs(parameters):
    if not {"StartDate", "EndDate"} <= parameters.keys():
        invalid("StartDate and EndDate are required")
    start, end = timestamp(parameters["StartDate"]), timestamp(parameters["EndDate"])
    if start > end:
        invalid("StartDate must not be later than EndDate")
    size = parameters.get("ResultSize", 100)
    if type(size) is not int or not 1 <= size <= 5000:
        invalid("ResultSize must be 1..5000")
    raw_mode = parameters.get("SessionCommand", "")
    mode = MODES.get(raw_mode.lower()) if isinstance(raw_mode, str) else None
    if raw_mode and mode is None:
        invalid("Unsupported SessionCommand")
    identifier = parameters.get("SessionId")
    if identifier is not None and (not identifier.strip() or len(identifier) > 1024):
        invalid("SessionId must be a nonempty bounded string")
    filters = {"StartDate": start.isoformat(), "EndDate": end.isoformat()}
    for key in ("Operations", "UserIds", "ObjectIds", "IPAddresses", "SiteIds"):
        if key in parameters:
            values = parameters[key]
            if not values or len(values) > 100 or any(not value for value in values):
                invalid("Audit filter lists require 1..100 nonempty values")
            if key == "IPAddresses":
                try:
                    values = [str(ipaddress.ip_address(value)) for value in values]
                except ValueError:
                    invalid("IPAddresses requires valid IP addresses")
            if key == "SiteIds":
                try:
                    values = [str(UUID(value)) for value in values]
                except (ValueError, TypeError, AttributeError):
                    invalid("SiteIds requires UUID values")
            filters[key] = sorted(set(value.casefold() for value in values))
    if "RecordType" in parameters:
        value = parameters["RecordType"]
        record_type = TYPES.get(value.lower()) or TYPE_IDS.get(value)
        if record_type is None:
            invalid("Unsupported audit RecordType")
        filters["RecordType"] = record_type
    if "FreeText" in parameters:
        value = parameters["FreeText"]
        if not value.strip():
            invalid("FreeText must not be empty")
        filters["FreeText"] = value.casefold()
    return filters, size, mode or "", identifier


async def authorize_search(session, actor):
    await require_exchange_command(session, actor, "Search-UnifiedAuditLog", tenant_wide=True)
    settings = await session.get(MailSettings, actor.tenant_id)
    if settings is None or settings.audit_tier not in {"standard", "premium"}:
        raise AuthorizationDenied("Audit entitlement is required")
    if actor.token_kind == "delegated":
        role = await session.scalar(
            select(AuditRole.user_id)
            .where(
                AuditRole.tenant_id == actor.tenant_id,
                AuditRole.user_id == actor.subject_id,
                AuditRole.role.in_(["Audit Logs", "View-Only Audit Logs"]),
            )
            .limit(1)
        )
        if role is None:
            raise AuthorizationDenied("Purview audit role is required")


def predicates(tenant_id, snapshot, filters):
    v = EvidenceVersion
    conditions = [
        v.tenant_id == tenant_id,
        v.generation == snapshot.generation,
        v.family == "auditLogRecord",
        v.valid_from <= snapshot.revision,
        or_(v.valid_to.is_(None), v.valid_to > snapshot.revision),
        v.event_time >= snapshot.retention_cutoff,
        v.event_time <= snapshot.evidence_time,
        v.available_at <= snapshot.evidence_time,
        v.event_time >= datetime.fromisoformat(filters["StartDate"]),
        v.event_time <= datetime.fromisoformat(filters["EndDate"]),
    ]
    if filters["StartDate"] == filters["EndDate"]:
        conditions.append(literal(False))
    for key, prop in {
        "Operations": "operation",
        "ObjectIds": "objectId",
        "IPAddresses": "clientIp",
    }.items():
        if key in filters:
            conditions.append(func.lower(v.payload[prop].as_string()).in_(filters[key]))
    if "UserIds" in filters:
        conditions.append(
            or_(
                func.lower(v.payload["userPrincipalName"].as_string()).in_(filters["UserIds"]),
                func.lower(v.payload["userId"].as_string()).in_(filters["UserIds"]),
            )
        )
    if "RecordType" in filters:
        conditions.append(v.payload["auditLogRecordType"].as_string() == filters["RecordType"])
    if "SiteIds" in filters:
        conditions.append(
            func.lower(v.payload["auditData"]["dynamicProperties"]["SiteId"].as_string()).in_(
                filters["SiteIds"]
            )
        )
    if "FreeText" in filters:
        conditions.append(
            func.lower(cast(v.payload, Text)).contains(filters["FreeText"], autoescape=True)
        )
    return conditions


def owner_key(actor):
    return hashlib.sha256(
        json.dumps(
            [
                str(actor.client_id),
                str(actor.subject_id),
                actor.token_kind,
            ]
        ).encode()
    ).hexdigest()


async def search_unified_audit(session, actor, parameters: dict, now: datetime) -> list[dict]:
    await authorize_search(session, actor)
    filters, page_size, mode, identifier = inputs(parameters)
    snapshot = await snapshot_evidence(session, actor.tenant_id, now)
    # EvidenceState lock serializes generation changes and first-use/session advancement.
    key = hashlib.sha256((identifier or str(uuid4())).encode()).hexdigest()
    row = await session.get(UnifiedAuditSession, (actor.tenant_id, key), with_for_update=True)
    fingerprint = hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()
    owner = owner_key(actor)
    if row is not None:
        if row.owner != owner:
            raise AuthorizationDenied("Audit session belongs to another principal")
        if row.generation != snapshot.generation or row.expires_at <= now:
            raise GraphError(410, "AuditSessionExpired", "Start a new audit session")
        if row.fingerprint != fingerprint:
            invalid("Audit session filters must not change")
        if row.first_mode != mode:
            row.mixed_mode = True
    else:
        active = await session.scalar(
            select(func.count())
            .select_from(UnifiedAuditSession)
            .where(
                UnifiedAuditSession.tenant_id == actor.tenant_id,
                UnifiedAuditSession.expires_at > now,
            )
        )
        if active >= 1000:
            error = GraphError(429, "TooManyRequests", "Local audit session quota reached")
            error.headers["Retry-After"] = "60"
            raise error
        # Ordering is guaranteed only for preview mode, although stable storage also
        # gives deterministic local large-set pages without promising service ordering.
        selected = (
            select(
                EvidenceVersion.payload.label("payload"),
                EvidenceVersion.event_time.label("event_time"),
                EvidenceVersion.record_id.label("record_id"),
            )
            .where(*predicates(actor.tenant_id, snapshot, filters))
            .order_by(EvidenceVersion.event_time, EvidenceVersion.record_id)
            .limit(50000 if mode else 5000)
            .subquery()
        )
        total, byte_count = (
            await session.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(func.octet_length(cast(selected.c.payload, Text))), 0),
                ).select_from(selected)
            )
        ).one()
        if byte_count > MAX_SNAPSHOT_BYTES:
            raise GraphError(413, "AuditSnapshotTooLarge", "Narrow the local audit search window")
        row = UnifiedAuditSession(
            tenant_id=actor.tenant_id,
            id=key,
            owner=owner,
            generation=snapshot.generation,
            fingerprint=fingerprint,
            first_mode=mode,
            mixed_mode=False,
            position=0,
            record_count=total,
            created_at=now,
            expires_at=now + SESSION_TTL,
        )
        session.add(row)
        await session.flush()
        await session.execute(
            insert(UnifiedAuditItem).from_select(
                ["tenant_id", "session_id", "position", "payload"],
                select(
                    literal(actor.tenant_id),
                    literal(key),
                    func.row_number().over(order_by=(selected.c.event_time, selected.c.record_id)),
                    selected.c.payload,
                ),
            )
        )
    cap = 10000 if row.mixed_mode else (50000 if mode == "ReturnLargeSet" else 5000)
    count = min(cap, row.record_count)
    stop = min(row.position + page_size, count)
    items = list(
        await session.scalars(
            select(UnifiedAuditItem)
            .where(
                UnifiedAuditItem.tenant_id == actor.tenant_id,
                UnifiedAuditItem.session_id == key,
                UnifiedAuditItem.position > row.position,
                UnifiedAuditItem.position <= stop,
            )
            .order_by(UnifiedAuditItem.position)
        )
    )
    result = []
    for item in items:
        payload = item.payload
        data = from_graph(payload, actor.tenant_id)
        formatted = parameters.get("Formatted", False)
        result.append(
            {
                "Identity": payload["id"],
                "CreationDate": payload["createdDateTime"],
                "RecordType": payload["auditLogRecordType"] if formatted else data["RecordType"],
                "UserIds": [data["UserId"]],
                "Operations": [data["Operation"]],
                "AuditData": json.dumps(data, ensure_ascii=False, indent=2 if formatted else None),
                "ResultIndex": item.position,
                "ResultCount": count,
                "AuditSearchRequestMetadata": {"moreRecordsAvailable": stop < count},
            }
        )
    row.position = max(row.position, stop)
    if identifier is None:
        await session.delete(row)  # One-shot searches do not consume durable session quota.
    await session.flush()
    return result


async def cleanup_unified_audit(session, tenant_id, now, limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be 1..1000")
    from mockazure.evidence.repository import lock_state

    state = await lock_state(session, tenant_id)
    expired = select(UnifiedAuditSession.id).where(
        UnifiedAuditSession.tenant_id == tenant_id,
        or_(
            UnifiedAuditSession.expires_at <= now,
            UnifiedAuditSession.generation != state.generation,
        ),
    )
    keys = (
        await session.execute(
            select(UnifiedAuditItem.session_id, UnifiedAuditItem.position)
            .where(
                UnifiedAuditItem.tenant_id == tenant_id,
                UnifiedAuditItem.session_id.in_(expired),
            )
            .order_by(UnifiedAuditItem.session_id, UnifiedAuditItem.position)
            .limit(limit)
        )
    ).all()
    if keys:
        await session.execute(
            delete(UnifiedAuditItem).where(
                UnifiedAuditItem.tenant_id == tenant_id,
                tuple_(UnifiedAuditItem.session_id, UnifiedAuditItem.position).in_(keys),
            )
        )
    # Retain small session tombstones for seven days, then allow caller-chosen ID reuse.
    remaining_budget = limit - len(keys)
    removed = 0
    if remaining_budget:
        old_ids = list(
            await session.scalars(
                select(UnifiedAuditSession.id)
                .where(
                    UnifiedAuditSession.tenant_id == tenant_id,
                    UnifiedAuditSession.expires_at <= now - timedelta(days=7),
                    ~select(UnifiedAuditItem.position)
                    .where(
                        UnifiedAuditItem.tenant_id == UnifiedAuditSession.tenant_id,
                        UnifiedAuditItem.session_id == UnifiedAuditSession.id,
                    )
                    .exists(),
                )
                .limit(remaining_budget)
            )
        )
        if old_ids:
            await session.execute(
                delete(UnifiedAuditSession).where(
                    UnifiedAuditSession.tenant_id == tenant_id,
                    UnifiedAuditSession.id.in_(old_ids),
                )
            )
            removed = len(old_ids)
    remaining = bool(
        await session.scalar(
            select(
                select(UnifiedAuditItem.position)
                .where(
                    UnifiedAuditItem.tenant_id == tenant_id,
                    UnifiedAuditItem.session_id.in_(expired),
                )
                .exists()
            )
        )
    )
    if not remaining:
        remaining = bool(
            await session.scalar(
                select(
                    select(UnifiedAuditSession.id)
                    .where(
                        UnifiedAuditSession.tenant_id == tenant_id,
                        UnifiedAuditSession.expires_at <= now - timedelta(days=7),
                    )
                    .exists()
                )
            )
        )
    return {"deleted": len(keys) + removed, "remaining": remaining}
