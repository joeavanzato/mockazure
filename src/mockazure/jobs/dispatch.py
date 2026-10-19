"""Explicit typed handlers preserve the export wire contract."""

from datetime import datetime, timedelta
from time import monotonic

from sqlalchemy.exc import SQLAlchemyError

from mockazure.activity.publication import process_activity
from mockazure.auditlog.worker import process_audit_query
from mockazure.calendar.meetings import process_meeting_delivery
from mockazure.db.engine import Database
from mockazure.evidence.projection import process_projection
from mockazure.jobs.repository import fail_job, finish_export
from mockazure.jobs.schemas import JobClaim, JobResult, parse_export
from mockazure.mail.send import process_mail_delivery
from mockazure.monitor.publication import process_diagnostic_delivery
from mockazure.notifications.delivery import process_notification
from mockazure.population.runner import process_population
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.operations import process_arm_delete
from mockazure.providers.arm.provider_jobs import process_provider_operation
from mockazure.security.password_reset import process_password_reset


async def process_export(database: Database, claim: JobClaim, wall_now: datetime) -> JobResult:
    snapshot = parse_export(claim.payload)
    if snapshot.source_tenant_id != claim.tenant_id:
        raise ValueError("Export tenant mismatch")
    async with database.sessions.begin() as session:
        done = await finish_export(session, claim, snapshot, now=wall_now)
    return JobResult("succeeded" if done else "stale", snapshot.model_dump(mode="json"), None)



HANDLERS = {
    "population.apply": process_population,
    "monitor.diagnostic": process_diagnostic_delivery,
    "graph.notification": process_notification,
    "calendar.meeting": process_meeting_delivery,
    "mail.delivery": process_mail_delivery,
    "authentication.passwordReset": process_password_reset,
    "activity.publish": process_activity,
    "tenant.export": process_export,
    "evidence.project": process_projection,
    "audit.query": process_audit_query,
    "arm.group.delete": process_arm_delete,
    "arm.provider.operation": process_provider_operation,
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
    except ArmError as error:
        code, terminal = error.code, True
    except SQLAlchemyError:
        code = "TransientError"
    except (ValueError, TypeError, KeyError):
        code = {
            "monitor.diagnostic": "DiagnosticDeliveryFailed",
            "graph.notification": "NotificationDeliveryFailed",
            "calendar.meeting": "MeetingDeliveryFailed",
            "mail.delivery": "MailDeliveryFailed",
            "evidence.project": "InvalidEvidenceSource",
            "activity.publish": "InvalidActivitySource",
            "authentication.passwordReset": "PasswordResetFailed",
            "audit.query": "AuditQueryFailed",
            "arm.group.delete": "ArmOperationFailed",
            "arm.provider.operation": "ArmOperationFailed",
        }.get(claim.kind, "ExportFailed")
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
