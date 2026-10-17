"""Terminal ARM job state and immutable failure facts commit together."""

from datetime import timedelta
from time import monotonic
from uuid import UUID

from sqlalchemy import select

from mockazure.db.job_model import Job
from mockazure.evidence.clock import evidence_now
from mockazure.evidence.projection import LeaseExpired
from mockazure.evidence.repository import append_source, lock_state
from mockazure.evidence.schemas import EvidenceSettings, SourceInput, SourceRef
from mockazure.evidence.sources import enqueue_projection
from mockazure.jobs.repository import fenced
from mockazure.providers.arm.inventory import lock_subscription
from mockazure.providers.arm.models import ArmResource
from mockazure.providers.arm.scopes import parse_scope
from mockazure.security.repository import lock_security


async def fail_arm_delete(
    session, identifier, now, *, claim=None, terminal=True, error_code="WorkerLeaseExpired"
):
    """Same security→evidence→subscription→job lock order as successful deletion."""
    started = monotonic()

    def checked_now():
        return now + timedelta(seconds=monotonic() - started)

    candidate = await session.get(Job, identifier)
    if candidate is None or candidate.kind != "arm.group.delete":
        return False
    scope = parse_scope(candidate.payload["scope"])
    tenant = candidate.tenant_id
    try:
        async with session.begin_nested():
            await lock_security(session, tenant)
            state = await lock_state(session, tenant)
            subscription = await lock_subscription(session, tenant, scope.subscription_id)
            conditions = (
                fenced(claim, checked_now())
                if claim
                else (
                    Job.id == identifier,
                    Job.kind == "arm.group.delete",
                    Job.state == "running",
                    Job.attempts >= 3,
                    Job.lease_until <= now,
                )
            )
            row = await session.scalar(
                select(Job)
                .where(*conditions)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None or (claim and row.lease_until <= checked_now()):
                return False
            expired_generation = UUID(row.payload["generation"]) != state.generation
            final = terminal or row.attempts >= 3 or expired_generation
            if expired_generation:
                resource = await session.get(ArmResource, (tenant, scope.key))
                if resource and resource.deletion_job_id == row.id:
                    resource.provisioning_state = "Succeeded"
                    resource.deletion_job_id = None
                    resource.revision += 1
                    subscription.revision += 1
                error_code = "StaleEvidenceGeneration"
            elif final:
                when = await evidence_now(session, tenant, now)
                ref = SourceRef(tenant_id=tenant, kind="arm", id=row.id)
                await append_source(
                    session,
                    SourceInput(
                        ref=ref,
                        occurred_at=when,
                        available_at=when
                        + timedelta(
                            seconds=EvidenceSettings.model_validate(state.settings).delay_seconds
                        ),
                        facts={
                            "operation": "Microsoft.Resources/subscriptions/resourceGroups/delete",
                            "scope": scope.display_id,
                            "actor": row.payload["actor"],
                            "before": row.payload["before"],
                            "after": row.payload["before"],
                            "correlation_id": row.payload["correlation_id"],
                            "provenance": "native",
                            "status": "Failed",
                        },
                    ),
                )
                await enqueue_projection(session, ref)
            await session.flush()
            if claim and row.lease_until <= checked_now():
                raise LeaseExpired()
            row.state = "failed" if final else "pending"
            row.error_code = error_code
            row.available_at = now + timedelta(seconds=row.attempts)
            row.lease_owner = row.lease_until = None
            row.updated_at = checked_now()
            if claim is None:
                row.claim_epoch += 1
            await session.flush()
            return True
    except LeaseExpired:
        return False
