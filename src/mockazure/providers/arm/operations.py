"""Durable ARM group deletion with generation and lease fencing."""

from datetime import UTC, datetime, timedelta
from time import monotonic
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from sqlalchemy import delete, select

from mockazure.db.job_model import Job
from mockazure.evidence.clock import evidence_now
from mockazure.evidence.projection import LeaseExpired
from mockazure.evidence.repository import append_source, lock_state
from mockazure.evidence.risk import actor_snapshot
from mockazure.evidence.schemas import EvidenceSettings, SourceInput, SourceRef
from mockazure.evidence.sources import enqueue_projection
from mockazure.jobs.repository import fenced
from mockazure.jobs.schemas import JobResult
from mockazure.providers.arm.authorization import grant_query, require_action
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import inventory_view, lock_subscription, resolve_scope
from mockazure.providers.arm.inventory_routes import request_scope
from mockazure.providers.arm.models import ArmResource, RoleAssignment
from mockazure.providers.arm.mutations import transaction
from mockazure.providers.arm.paging import parameters
from mockazure.providers.arm.probes import principal
from mockazure.providers.arm.scopes import parse_scope
from mockazure.security.repository import lock_security

router = APIRouter()
ACTION = "Microsoft.Resources/subscriptions/resourceGroups/delete"


def operation_link(request, subscription, identifier):
    profile = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
    return (
        profile.arm
        + f"/subscriptions/{subscription}/providers/microsoft.resources/operations/"
        + str(identifier)
        + "?"
        + urlencode({"api-version": "2021-04-01"})
    )


@router.delete("/subscriptions/{subscription}/resourcegroups/{group}")
async def start_delete(subscription: str, group: str, request: Request):
    parameters(request, "groups.delete")
    scope = request_scope(subscription, group)
    async with transaction(request, scope) as (session, actor, sub):
        await require_action(session, actor, scope, ACTION)
        row = await resolve_scope(session, actor.tenant_id, scope)
        from mockazure.providers.arm.provider_jobs import require_unreferenced, subtree

        await require_unreferenced(
            session, actor.tenant_id, await subtree(session, actor.tenant_id, scope)
        )
        prior = await session.get(Job, row.deletion_job_id) if row.deletion_job_id else None
        if prior and prior.state in {"pending", "running"}:
            identifier = prior.id
        else:
            if prior and prior.state == "failed":
                # An explicit new DELETE retries a terminally failed operation.
                row.provisioning_state = "Succeeded"
            state = await lock_state(session, actor.tenant_id)
            identifier = uuid4()
            session.add(
                Job(
                    id=identifier,
                    tenant_id=actor.tenant_id,
                    kind="arm.group.delete",
                    payload={
                        "scope": scope.display_id,
                        "generation": str(state.generation),
                        "actor": await actor_snapshot(session, actor),
                        "before": inventory_view(row),
                        "correlation_id": str(uuid4()),
                    },
                )
            )
            await session.flush()
            row.deletion_job_id = identifier
            row.provisioning_state = "Deleting"
            row.changed_at = datetime.now(UTC)
            row.revision += 1
            sub.revision += 1
        return Response(
            status_code=202,
            headers={
                "Location": operation_link(request, subscription, identifier),
                "Retry-After": "1",
            },
        )


@router.get("/subscriptions/{subscription}/providers/microsoft.resources/operations/{identifier}")
async def poll_delete(subscription: str, identifier: str, request: Request):
    parameters(request, "groups.delete")
    sub_scope = request_scope(subscription)
    try:
        job_id = UUID(identifier)
    except ValueError:
        raise ArmError(400, "InvalidOperationId", "Invalid ARM operation identity") from None
    async with request.app.state.database.sessions() as session:
        actor = await principal(request, session)
        await resolve_scope(session, actor.tenant_id, sub_scope)
        row = await session.get(Job, job_id)
        if row is None or row.tenant_id != actor.tenant_id or row.kind != "arm.group.delete":
            raise ArmError(404, "OperationNotFound", "ARM operation was not found")
        scope = parse_scope(row.payload["scope"])
        if scope.subscription_id != sub_scope.subscription_id:
            raise ArmError(404, "OperationNotFound", "ARM operation was not found")
        allowed = await grant_query(session, actor, ACTION)
        if not await session.scalar(
            allowed.where(RoleAssignment.scope_key.in_((*scope.parent_keys, scope.key))).limit(1)
        ):
            raise ArmError(403, "AuthorizationFailed", "ARM operation access was revoked")
        if row.state == "failed":
            raise ArmError(
                409, row.error_code or "OperationFailed", "Resource group deletion failed"
            )
        if row.state == "succeeded":
            return Response(status_code=200)
        return Response(
            status_code=202,
            headers={
                "Location": operation_link(request, subscription, identifier),
                "Retry-After": "1",
            },
        )


async def process_arm_delete(database, claim, wall_now):
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    if claim.kind != "arm.group.delete" or set(claim.payload) != {
        "scope",
        "generation",
        "actor",
        "before",
        "correlation_id",
    }:
        raise ValueError("Invalid ARM deletion job")
    scope = parse_scope(claim.payload["scope"])
    if scope.group is None or scope.resource_parts:
        raise ValueError("Deletion requires a resource group")
    generation = UUID(claim.payload["generation"])
    UUID(claim.payload["correlation_id"])
    try:
        async with database.sessions.begin() as session:
            await lock_security(session, claim.tenant_id)
            state = await lock_state(session, claim.tenant_id)
            sub = await lock_subscription(session, claim.tenant_id, scope.subscription_id)
            from mockazure.cloud.events import lock_cloud_state

            await lock_cloud_state(session, claim.tenant_id)
            job = await session.scalar(select(Job).where(*fenced(claim, now())).with_for_update())
            if job is None or job.lease_until <= now():
                return JobResult("stale", None, None)
            row = await session.get(ArmResource, (claim.tenant_id, scope.key))
            if generation != state.generation:
                if row and row.deletion_job_id == claim.id:
                    row.provisioning_state = "Succeeded"
                    row.deletion_job_id = None
                    row.revision += 1
                    sub.revision += 1
                job.state, job.error_code = "failed", "StaleEvidenceGeneration"
                job.lease_owner = job.lease_until = None
                return JobResult("failed", None, job.error_code)
            if (
                row is None
                or row.deletion_job_id != claim.id
                or row.provisioning_state != "Deleting"
            ):
                raise ValueError("Deletion target no longer matches operation")
            from mockazure.providers.arm.provider_jobs import require_unreferenced, subtree

            await require_unreferenced(
                session, claim.tenant_id, await subtree(session, claim.tenant_id, scope)
            )
            from mockazure.keyvault.vault_recovery import archive_vaults

            await archive_vaults(
                session, claim.tenant_id, await subtree(session, claim.tenant_id, scope), now()
            )
            from sqlalchemy import func, update

            await session.execute(
                update(Job)
                .where(
                    Job.tenant_id == claim.tenant_id,
                    Job.kind == "arm.provider.operation",
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
                    ArmResource.tenant_id == claim.tenant_id, ArmResource.scope_key == scope.key
                )
            )
            sub.revision += 1
            when = await evidence_now(session, claim.tenant_id, now())
            ref = SourceRef(tenant_id=claim.tenant_id, kind="arm", id=claim.id)
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
                        "operation": ACTION,
                        "scope": scope.display_id,
                        "actor": claim.payload["actor"],
                        "before": claim.payload["before"],
                        "after": {},
                        "correlation_id": claim.payload["correlation_id"],
                        "provenance": "native",
                        "status": "Succeeded",
                    },
                ),
            )
            await enqueue_projection(session, ref)
            from mockazure.cloud.events import append_service_event
            from mockazure.cloud.types import ServiceEvent

            await append_service_event(
                session,
                ServiceEvent(
                    claim.id,
                    claim.tenant_id,
                    scope.display_id,
                    "arm",
                    "Administrative",
                    ACTION,
                    when,
                    {"status": "Succeeded"},
                ),
            )
            await session.flush()
            if job.lease_until <= now():
                raise LeaseExpired()
            job.state, job.result, job.error_code = "succeeded", {"status": "Succeeded"}, None
            job.lease_owner = job.lease_until = None
            job.updated_at = now()
            await session.flush()
            return JobResult("succeeded", job.result, None)
    except LeaseExpired:
        return JobResult("stale", None, None)
