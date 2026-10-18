"""Durable provider operations share the canonical inventory and job fences."""

from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

from sqlalchemy import delete, func, select, update

from mockazure.cloud.events import append_service_event, lock_cloud_state
from mockazure.cloud.types import ServiceEvent
from mockazure.db.engine import Database
from mockazure.db.job_model import Job
from mockazure.evidence.clock import evidence_now
from mockazure.evidence.projection import LeaseExpired
from mockazure.evidence.repository import append_source, lock_state
from mockazure.evidence.risk import actor_snapshot
from mockazure.evidence.schemas import EvidenceSettings, SourceInput, SourceRef
from mockazure.evidence.sources import enqueue_projection
from mockazure.jobs.repository import fenced
from mockazure.jobs.schemas import JobClaim, JobResult
from mockazure.providers.arm.authorization import require_action
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import inventory_view, lock_subscription, resolve_scope
from mockazure.providers.arm.models import (
    ArmResource,
    ProviderResource,
    ResourceRelationship,
    VmState,
)
from mockazure.providers.arm.scopes import parse_scope
from mockazure.security.repository import lock_security

KIND = "arm.provider.operation"
POWER = {
    "start": "running",
    "restart": "running",
    "powerOff": "stopped",
    "deallocate": "deallocated",
}


async def subtree(session, tenant, scope):
    return list(
        await session.scalars(
            select(ArmResource.scope_key).where(
                ArmResource.tenant_id == tenant,
                (ArmResource.scope_key == scope.key)
                | ArmResource.scope_key.startswith(scope.key + "/", autoescape=True),
            )
        )
    )


async def require_unreferenced(session, tenant, keys):
    if await session.scalar(
        select(ResourceRelationship.source_key)
        .where(
            ResourceRelationship.tenant_id == tenant,
            ResourceRelationship.target_key.in_(keys),
            ResourceRelationship.source_key.not_in(keys),
        )
        .limit(1)
    ):
        raise ArmError(409, "ResourceInUse", "Another resource references the deletion target")


async def start_operation(session, actor, scope, operation, version):
    row = await resolve_scope(session, actor.tenant_id, scope)
    marker = await session.get(ProviderResource, (actor.tenant_id, scope.key))
    if marker is None or (operation != "delete" and marker.family != "vm"):
        raise ArmError(409, "InvalidOperation", "Operational provider state is required")
    action = row.resource_type + (
        "/delete" if operation == "delete" else "/" + operation + "/action"
    )
    await require_action(session, actor, scope, action)
    group = await session.get(ArmResource, (actor.tenant_id, row.group_key))
    if group.provisioning_state == "Deleting":
        raise ArmError(409, "ResourceGroupBeingDeleted", "Resource group deletion is in progress")
    if row.deletion_job_id:
        prior = await session.get(Job, row.deletion_job_id)
        if prior and prior.state in {"pending", "running"}:
            if prior.kind == KIND and prior.payload["operation"] == operation:
                return prior
            raise ArmError(409, "OperationInProgress", "Another provider operation is in progress")
    if operation == "delete":
        await require_unreferenced(
            session, actor.tenant_id, await subtree(session, actor.tenant_id, scope)
        )
    state = await lock_state(session, actor.tenant_id)
    cloud = await lock_cloud_state(session, actor.tenant_id)
    now = datetime.now(UTC)
    job = Job(
        id=uuid4(),
        tenant_id=actor.tenant_id,
        kind=KIND,
        payload={
            "scope": scope.display_id,
            "operation": operation,
            "action": action,
            "version": version,
            "namespace": row.resource_type.split("/")[0],
            "location": row.location,
            "generation": str(state.generation),
            "cloud_generation": cloud.generation,
            "actor": await actor_snapshot(session, actor),
            "before": inventory_view(row),
        },
    )
    session.add(job)
    await session.flush()
    row.deletion_job_id = job.id
    row.provisioning_state = "Deleting" if operation == "delete" else "Updating"
    row.revision += 1
    row.changed_at = now
    sub = await lock_subscription(session, actor.tenant_id, scope.subscription_id)
    sub.revision += 1
    return job


async def append_completion(session, job, state, when, after, status):
    ref = SourceRef(tenant_id=job.tenant_id, kind="arm", id=job.id)
    await append_source(
        session,
        SourceInput(
            ref=ref,
            occurred_at=when,
            available_at=when
            + timedelta(seconds=EvidenceSettings.model_validate(state.settings).delay_seconds),
            facts={
                "operation": job.payload["action"],
                "scope": job.payload["scope"],
                "actor": job.payload["actor"],
                "before": job.payload["before"],
                "after": after,
                "correlation_id": str(job.id),
                "provenance": "native",
                "status": status,
            },
        ),
    )
    await enqueue_projection(session, ref)
    await append_service_event(
        session,
        ServiceEvent(
            job.id,
            job.tenant_id,
            job.payload["scope"],
            "arm",
            "Administrative",
            job.payload["action"],
            when,
            {"status": status},
        ),
    )


async def process_provider_operation(
    database: Database, claim: JobClaim, wall_now: datetime
) -> JobResult:
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    if claim.kind != KIND or claim.payload.get("operation") not in {*POWER, "delete"}:
        raise ValueError("Invalid provider job")
    scope = parse_scope(claim.payload["scope"])
    try:
        async with database.sessions.begin() as session:
            await lock_security(session, claim.tenant_id)
            state = await lock_state(session, claim.tenant_id)
            sub = await lock_subscription(session, claim.tenant_id, scope.subscription_id)
            cloud = await lock_cloud_state(session, claim.tenant_id)
            job = await session.scalar(select(Job).where(*fenced(claim, now())).with_for_update())
            if job is None or job.lease_until <= now():
                return JobResult("stale", None, None)
            row = await session.get(ArmResource, (claim.tenant_id, scope.key))
            if (
                str(state.generation) != job.payload["generation"]
                or cloud.generation != job.payload["cloud_generation"]
            ):
                if row and row.deletion_job_id == job.id:
                    row.provisioning_state = "Succeeded"
                    row.deletion_job_id = None
                    row.revision += 1
                    sub.revision += 1
                job.state = "failed"
                job.error_code = "StaleCloudGeneration"
                job.lease_owner = job.lease_until = None
                return JobResult("failed", None, job.error_code)
            if row is None or row.deletion_job_id != job.id:
                raise ValueError("Provider target changed")
            if job.payload["operation"] == "delete":
                keys = await subtree(session, claim.tenant_id, scope)
                await require_unreferenced(session, claim.tenant_id, keys)
                await session.execute(
                    update(Job)
                    .where(
                        Job.tenant_id == claim.tenant_id,
                        Job.kind == KIND,
                        Job.id != job.id,
                        Job.state.in_(("pending", "running")),
                        func.lower(Job.payload["scope"].astext).startswith(
                            scope.key + "/", autoescape=True
                        ),
                    )
                    .values(
                        state="failed",
                        error_code="ParentDeleted",
                        lease_owner=None,
                        lease_until=None,
                        claim_epoch=Job.claim_epoch + 1,
                        updated_at=now(),
                    )
                )
                await session.execute(
                    delete(ArmResource).where(
                        ArmResource.tenant_id == claim.tenant_id, ArmResource.scope_key.in_(keys)
                    )
                )
                after = {}
            else:
                vm = await session.get(VmState, (claim.tenant_id, scope.key))
                if vm is None:
                    raise ValueError("Missing VM state")
                vm.power_state = POWER[job.payload["operation"]]
                row.provisioning_state = "Succeeded"
                row.deletion_job_id = None
                row.changed_at = now()
                row.revision += 1
                after = inventory_view(row)
            sub.revision += 1
            when = await evidence_now(session, claim.tenant_id, now())
            await append_completion(session, job, state, when, after, "Succeeded")
            await session.flush()
            if job.lease_until <= now():
                raise LeaseExpired()
            job.state = "succeeded"
            job.result = {"status": "Succeeded"}
            job.error_code = None
            job.lease_owner = job.lease_until = None
            job.updated_at = now()
            await session.flush()
            return JobResult("succeeded", job.result, None)
    except LeaseExpired:
        return JobResult("stale", None, None)


async def fail_provider_operation(
    session, identifier, now, *, claim=None, terminal=True, error_code="WorkerLeaseExpired"
):
    started = monotonic()

    def checked_now():
        return now + timedelta(seconds=monotonic() - started)

    try:
        async with session.begin_nested():
            candidate = await session.get(Job, identifier)
            if candidate is None or candidate.kind != KIND:
                return False
            tenant = candidate.tenant_id
            scope = parse_scope(candidate.payload["scope"])
            await lock_security(session, tenant)
            state = await lock_state(session, tenant)
            sub = await lock_subscription(session, tenant, scope.subscription_id)
            cloud = await lock_cloud_state(session, tenant)
            conditions = (
                fenced(claim, checked_now())
                if claim
                else (
                    Job.id == identifier,
                    Job.kind == KIND,
                    Job.state == "running",
                    Job.attempts >= 3,
                    Job.lease_until <= now,
                )
            )
            job = await session.scalar(
                select(Job)
                .where(*conditions)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if job is None or (claim and job.lease_until <= checked_now()):
                return False
            stale = (
                str(state.generation) != job.payload["generation"]
                or cloud.generation != job.payload["cloud_generation"]
            )
            final = terminal or job.attempts >= 3 or stale
            if final:
                row = await session.get(ArmResource, (tenant, scope.key))
                if row and row.deletion_job_id == job.id:
                    row.provisioning_state = "Succeeded" if stale else "Failed"
                    row.deletion_job_id = None
                    row.revision += 1
                    sub.revision += 1
                if not stale:
                    await append_completion(
                        session,
                        job,
                        state,
                        await evidence_now(session, tenant, now),
                        inventory_view(row) if row else {},
                        "Failed",
                    )
            await session.flush()
            if claim and claim.lease_until <= checked_now():
                raise LeaseExpired()
            job.state = "failed" if final else "pending"
            job.error_code = "StaleCloudGeneration" if stale else error_code
            job.available_at = now + timedelta(seconds=job.attempts)
            job.lease_owner = job.lease_until = None
            job.updated_at = checked_now()
            if claim is None:
                job.claim_epoch += 1
            await session.flush()
            if claim and claim.lease_until <= checked_now():
                raise LeaseExpired()
            return True
    except LeaseExpired:
        return False
