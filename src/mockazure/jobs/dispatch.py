"""Explicit typed handlers preserve the export wire contract."""

from datetime import datetime, timedelta
from time import monotonic

from sqlalchemy.exc import SQLAlchemyError

from mockazure.auditlog.worker import process_audit_query
from mockazure.db.engine import Database
from mockazure.evidence.projection import process_projection
from mockazure.jobs.repository import fail_job, finish_export
from mockazure.jobs.schemas import ExportSnapshot, JobClaim, JobResult


async def process_export(database: Database, claim: JobClaim, wall_now: datetime) -> JobResult:
    snapshot = ExportSnapshot.model_validate(claim.payload)
    if snapshot.source_tenant_id != claim.tenant_id:
        raise ValueError("Export tenant mismatch")
    async with database.sessions.begin() as session:
        done = await finish_export(session, claim, snapshot, now=wall_now)
    return JobResult("succeeded" if done else "stale", snapshot.model_dump(mode="json"), None)


HANDLERS = {
    "tenant.export": process_export,
    "evidence.project": process_projection,
    "audit.query": process_audit_query,
}


async def dispatch_job(database: Database, claim: JobClaim, wall_now: datetime) -> JobResult:
    started = monotonic()
    terminal = False
    handler = HANDLERS.get(claim.kind)
    try:
        if handler is None:
            code, terminal = "UnsupportedJobKind", True
        else:
            return await handler(database, claim, wall_now)
    except SQLAlchemyError:
        code = "TransientError"
    except (ValueError, TypeError, KeyError):
        code = {"evidence.project": "InvalidEvidenceSource", "audit.query": "AuditQueryFailed"}.get(
            claim.kind, "ExportFailed"
        )
        terminal = True
    async with database.sessions.begin() as session:
        changed = await fail_job(
            session,
            claim,
            error_code=code,
            now=wall_now + timedelta(seconds=monotonic() - started),
            terminal=terminal,
        )
    if not changed:
        return JobResult("stale", None, None)
    return JobResult("failed" if terminal or claim.attempt >= 3 else "pending", None, code)
