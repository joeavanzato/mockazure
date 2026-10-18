"""Durable reset operations persist only verifier material, never a plaintext password."""

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.db.job_model import Job
from mockazure.identity.credentials import hash_secret
from mockazure.identity.types import PrincipalContext
from mockazure.jobs.repository import lock_claim
from mockazure.jobs.schemas import JobResult
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.security.audit import record_security_audit
from mockazure.security.method_mutations import WRITE_ROLES, require_target_authority
from mockazure.security.methods import invalid, method_user
from mockazure.security.permissions import forbidden
from mockazure.security.repository import lock_security, missing
from mockazure.security.reset_model import PasswordResetOperation

RESET_ROLES = WRITE_ROLES | {
    "User Administrator",
    "Helpdesk Administrator",
    "Password Administrator",
}


@dataclass(frozen=True)
class ResetReceipt:
    operation_id: UUID
    generated_password: str | None


def validate_password(value):
    try:
        valid = (
            isinstance(value, str)
            and "\0" not in value
            and 12 <= len(value.encode("utf-8")) <= 4096
        )
    except UnicodeError:
        valid = False
    if not valid:
        invalid("A local password must contain 12 to 4096 UTF-8 bytes and no NUL")
    return value


async def reset_audit(session, row, user, previous):
    actor = PrincipalContext(
        row.tenant_id,
        row.client_id,
        row.actor_id,
        "delegated",
        "00000003-0000-0000-c000-000000000000",
        frozenset(),
    )
    await record_security_audit(
        session,
        actor,
        "Reset user password",
        {
            "id": str(row.id),
            "displayName": user.display_name,
            "userPrincipalName": user.upn,
            "type": "longRunningOperation",
        },
        {"status": previous} if previous else None,
        {"status": row.status},
        datetime.now(UTC),
    )


async def reconcile_failed(session, row):
    job = await session.get(Job, row.id)
    if row.status in {"notStarted", "running"} and job and job.state == "failed":
        row.status, row.status_detail, row.verifier = (
            "failed",
            "The local reset worker failed",
            None,
        )
        row.updated_at = datetime.now(UTC)
        await session.flush()


async def start_password_reset(
    session, context, user_id: UUID, new_password: str | None
) -> ResetReceipt:
    await lock_security(session, context.tenant_id)
    user = await method_user(session, context.tenant_id, user_id)
    await permitted(
        context,
        OperationRequirement(
            "graph",
            frozenset(),
            frozenset({"UserAuthenticationMethod.ReadWrite.All"}),
            frozenset(RESET_ROLES),
        ),
        session,
    )
    if context.subject_id == user.id:
        forbidden()
    await require_target_authority(session, context, user, reset=True)
    if user.profile.get("onPremisesSyncEnabled"):
        raise GraphError(501, "NotSupported", "Hybrid password writeback is not implemented")
    pending = await session.scalar(
        select(PasswordResetOperation).where(
            PasswordResetOperation.tenant_id == context.tenant_id,
            PasswordResetOperation.user_id == user.id,
            PasswordResetOperation.status.in_(("notStarted", "running")),
        )
    )
    if pending:
        await reconcile_failed(session, pending)
        if pending.status != "failed":
            raise GraphError(409, "Request_Conflict", "A password reset is already pending")
    generated = secrets.token_urlsafe(32) if new_password is None else None
    verifier = await asyncio.to_thread(hash_secret, validate_password(generated or new_password))
    now, identifier = datetime.now(UTC), uuid4()
    row = PasswordResetOperation(
        id=identifier,
        tenant_id=context.tenant_id,
        user_id=user.id,
        client_id=context.client_id,
        actor_id=context.subject_id,
        status="notStarted",
        status_detail="",
        verifier=verifier,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.add(
        Job(
            id=identifier,
            tenant_id=context.tenant_id,
            kind="authentication.passwordReset",
            source_key=str(identifier),
            payload={"operation_id": str(identifier)},
        )
    )
    await reset_audit(session, row, user, None)
    await session.flush()
    return ResetReceipt(identifier, generated)


async def finish_password_reset(session, operation_id: UUID) -> None:
    row = await session.get(PasswordResetOperation, operation_id)
    if row is None:
        missing()
    await lock_security(session, row.tenant_id)
    await session.refresh(row)
    if row.status in {"succeeded", "failed"}:
        return
    from mockazure.identity.models import User

    user = await session.get(User, row.user_id, populate_existing=True)
    if user is None or user.deleted_at or user.profile.get("onPremisesSyncEnabled"):
        row.status, row.status_detail = "failed", "The cloud user is no longer available"
    else:
        user.verifier = row.verifier
        user.password_change_required = True
        user.credential_version += 1
        row.status, row.status_detail = "succeeded", "Password reset completed"
        await reset_audit(session, row, user, "notStarted")
    row.verifier, row.updated_at = None, datetime.now(UTC)
    await session.flush()


class ResetLeaseExpired(Exception):
    pass


async def process_password_reset(database, claim, wall_now):
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    try:
        async with database.sessions.begin() as session:
            await lock_security(session, claim.tenant_id)
            job = await lock_claim(session, claim, now())
            if job is None:
                return JobResult("stale", None, None)
            identifier = UUID(claim.payload["operation_id"])
            row = await session.get(PasswordResetOperation, identifier)
            if row is None or row.tenant_id != claim.tenant_id or row.id != claim.id:
                raise ValueError("Invalid password reset job")
            await finish_password_reset(session, identifier)
            if job.lease_until <= now():
                raise ResetLeaseExpired()
            job.state = "succeeded" if row.status == "succeeded" else "failed"
            job.result = {"operation_id": str(identifier)}
            job.error_code = None if job.state == "succeeded" else "PasswordResetFailed"
            job.lease_owner = job.lease_until = None
            job.updated_at = now()
            return JobResult(job.state, job.result, job.error_code)
    except ResetLeaseExpired:
        return JobResult("stale", None, None)
