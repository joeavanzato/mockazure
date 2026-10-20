"""One bounded checkpoint per lease; no transaction spans a public HTTP action."""

import asyncio
from datetime import timedelta
from time import monotonic
from uuid import UUID

from mockazure.admin.errors import AdminError
from mockazure.jobs.repository import lock_claim
from mockazure.jobs.schemas import JobResult
from mockazure.population.bindings import EntityBinding, bind
from mockazure.population.catalog import get_adapter
from mockazure.population.generators import VERSION, inventory
from mockazure.population.models import (
    OperationReceipt,
    OrganizationNamespace,
    PopulationRun,
    PopulationStep,
    ResourceBinding,
)
from mockazure.population.planner import bindings, mutations_for
from mockazure.population.protection import seal, unseal
from mockazure.population.tokens import fingerprint
from mockazure.population.types import AdapterContext, BatchResult


async def checkpoint(database, claim, now, change):
    async with database.sessions.begin() as session:
        job = await lock_claim(session, claim, now)
        if job is None:
            return False
        run = await session.get(PopulationRun, UUID(claim.payload["run_id"]), with_for_update=True)
        if run is None or run.tenant_id != claim.tenant_id or run.job_id != job.id:
            raise ValueError("Population job binding mismatch")
        delay_ms = await change(session, job, run)
        job.lease_owner = job.lease_until = None
        job.attempts = 0
        job.updated_at = now
        job.available_at = now + timedelta(milliseconds=50 if delay_ms is None else delay_ms)
        run.updated_at = now
        await session.flush()
        return True


async def process_population(database, claim, wall_now):
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    run_id = UUID(claim.payload["run_id"])
    receipt_key = None

    async def stop(state, error=None):
        async def change(session, job, run):
            run.state = "cancelled" if run.cancel_requested else state
            run.errors = [error] if error else []
            job.state = "failed" if run.state in {"failed", "needs-attention"} else "succeeded"

        changed = await checkpoint(database, claim, now(), change)
        return JobResult(state if changed else "stale", None, error)

    # Read immutable input and current position under the worker's fenced lease.
    async with database.sessions.begin() as session:
        job = await lock_claim(session, claim, now())
        if job is None:
            return JobResult("stale", None, None)
        run = await session.get(PopulationRun, run_id, with_for_update=True)
        if run is None or run.tenant_id != claim.tenant_id or run.job_id != job.id:
            raise ValueError("Population job binding mismatch")
        cancel = run.cancel_requested
        snapshot = unseal(database, run.encrypted)
        step_row = await session.get(PopulationStep, (run_id, run.cursor))
        namespace, ordinal = run.namespace, run.cursor
        refs = {
            "domain": snapshot["domain"],
            **await bindings(session, run.tenant_id, namespace),
            **snapshot["request"].get("identity_refs", {}),
        }
        if step_row:
            offset, step_key = step_row.offset, step_row.key
        run.state = "running"
    if cancel:
        return await stop("cancelled")
    if snapshot["generator_version"] != VERSION:
        return await stop(
            "needs-attention", "Generator version changed; use a compatible installation"
        )
    from mockazure.population.activity import VERSION as ACTIVITY_VERSION
    from mockazure.population.activity import is_activity_step

    if snapshot["request"].get("activity") and snapshot.get("activity_version") != ACTIVITY_VERSION:
        return await stop(
            "needs-attention", "Activity version changed; use a compatible installation"
        )
    if step_row is None:
        return await stop("succeeded")
    step = snapshot["resolved"]["steps"][step_key]
    adapter = get_adapter(step["family"])
    descriptor = next(o for o in adapter.describe().operations if o.key == step["operation"])
    ctx = AdapterContext(database, database.settings, claim.tenant_id)
    limit = 1 if descriptor.mode == "service" or step["family"] == "content.objects" else 100
    try:
        generator = step["parameters"]["generator"]
        version = inventory()[generator]["version"]
        expected_version = snapshot.get("generator_versions", {}).get(generator, VERSION)
        if version != expected_version:
            return await stop(
                "needs-attention", "Generator version changed; use a compatible installation"
            )
        if generator.startswith("business."):
            async with database.sessions() as session:
                organization = await session.get(
                    OrganizationNamespace, (claim.tenant_id, namespace)
                )
                if organization is None or str(organization.generation) != snapshot.get(
                    "organization_generation"
                ):
                    raise AdminError(
                        409, "NamespaceGenerationChanged", "Namespace generation changed"
                    )
        mutations = mutations_for(snapshot, step, refs, offset, limit)
        for mutation in mutations:
            adapter.validate(mutation)
        digest = fingerprint(
            {
                "family": step["family"],
                "version": version,
                "mutations": [
                    m.model_dump(mode="json", exclude={"expected_version"}) for m in mutations
                ],
            }
        )
        receipt_key = fingerprint([step_key, offset, len(mutations)])
        receipt_id = (claim.tenant_id, namespace, receipt_key)
        result, replay, prior_intent = None, False, False
        async with database.sessions.begin() as session:
            if await lock_claim(session, claim, now()) is None:
                return JobResult("stale", None, None)
            receipt = await session.get(OperationReceipt, receipt_id, with_for_update=True)
            if receipt:
                if receipt.input_digest != digest:
                    raise AdminError(
                        409, "NamespaceConflict", "Namespace inputs changed; use a new namespace"
                    )
                if receipt.encrypted_result:
                    result = BatchResult.model_validate(unseal(database, receipt.encrypted_result))
                    replay = receipt.origin_run != run_id or receipt.state == "verified"
                else:
                    prior_intent = True
            else:
                session.add(
                    OperationReceipt(
                        tenant_id=claim.tenant_id,
                        namespace=namespace,
                        key=receipt_key,
                        family=step["family"],
                        input_digest=digest,
                        state="intent",
                        origin_run=run_id,
                        updated_at=now(),
                    )
                )
        if prior_intent and descriptor.replay_policy == "manual":
            return await stop(
                "needs-attention",
                "Uncertain service outcome; reconcile it before creating a new run",
            )
        if result is None:
            if mutations:
                # The adapter remains insert-only unless an explicit resource edit was approved.
                timeout = min(18, (claim.lease_until - now()).total_seconds() - 5)
                if timeout <= 0:
                    return await stop(
                        "needs-attention", "Insufficient lease time for this operation"
                    )
                result = await asyncio.wait_for(adapter.apply(ctx, mutations), timeout=timeout)
            else:
                result = BatchResult(items=[], effects={"created": 0, "updated": 0, "unchanged": 0})
            # Acknowledged result is durable before verification, which can yield to publisher jobs.
            async with database.sessions.begin() as session:
                if await lock_claim(session, claim, now()) is None:
                    return JobResult("stale", None, None)
                receipt = await session.get(OperationReceipt, receipt_id, with_for_update=True)
                receipt.encrypted_result = seal(database, result.model_dump(mode="json"))
                receipt.state, receipt.updated_at = "acknowledged", now()
        timeout = min(8, (claim.lease_until - now()).total_seconds() - 2)
        if timeout <= 0:
            verification = None
        else:
            verification = await asyncio.wait_for(adapter.verify(ctx, result), timeout=timeout)
        if verification and verification.status == "failed":
            return await stop("needs-attention", "Readback no longer matches the recorded result")
        pending = verification is None or verification.status == "pending"

        async def complete(session, job, current):
            receipt = await session.get(OperationReceipt, receipt_id, with_for_update=True)
            cursor = await session.get(PopulationStep, (run_id, ordinal), with_for_update=True)
            if current.cancel_requested:
                current.state, job.state = "cancelled", "succeeded"
                return
            if pending:
                if (now() - receipt.updated_at).total_seconds() > 600:
                    current.state, job.state = "needs-attention", "failed"
                    current.errors = ["Readback publication exceeded ten minutes"]
                    return
                current.state, job.state, cursor.state = "verifying", "pending", "verifying"
                return
            receipt.state = "verified"
            if generator.startswith("business."):
                by_identity = {(item.parent, item.id): item for item in result.items}
                for mutation in mutations:
                    if descriptor.mode == "service":
                        if len(mutations) != 1 or len(result.items) != 1:
                            raise ValueError("Business service actions require one recorded result")
                        item = result.items[0]
                        service = item.properties["service"]
                    else:
                        base = getattr(adapter, "base", adapter)
                        payload = base.normalize(mutation)
                        identifier = str(payload[base.identity_field])
                        item = by_identity.get((mutation.parent, identifier))
                        service = (
                            "arm"
                            if step["family"] == "arm.inventory"
                            else "lab-identity"
                            if step["family"] == "identity.clients"
                            else "graph"
                        )
                    if item is None:
                        raise ValueError("Business result did not preserve the declared identity")
                    await bind(
                        session,
                        EntityBinding(
                            tenant_id=claim.tenant_id,
                            namespace=namespace,
                            logical_key=mutation.logical_key,
                            service=service,
                            actual_id=item.id,
                            parent_id=(
                                mutation.parent
                                if mutation.parent not in {item.id, "deviceManagement"}
                                else None
                            ),
                            generation=UUID(snapshot["organization_generation"]),
                        ),
                    )
            effects = (
                {"created": 0, "updated": 0, "unchanged": len(result.items)}
                if replay
                else result.effects
            )
            current.progress = {
                k: current.progress.get(k, 0) + effects.get(k, 0)
                for k in ("created", "updated", "unchanged", "verified")
            }
            current.progress["verified"] += len(result.items)
            if step.get("target") and offset == 0 and result.items:
                key = (claim.tenant_id, namespace, step["target"])
                binding = await session.get(ResourceBinding, key, with_for_update=True)
                identifier = result.items[0].id
                if binding and binding.actual_id != identifier:
                    raise AdminError(409, "BindingConflict", "Captured resource identity changed")
                if binding is None:
                    session.add(
                        ResourceBinding(
                            tenant_id=claim.tenant_id,
                            namespace=namespace,
                            key=step["target"],
                            actual_id=identifier,
                        )
                    )
            cursor.offset += len(mutations)
            if cursor.offset >= cursor.count:
                cursor.state = "succeeded"
                current.cursor += 1
            else:
                cursor.state = "running"
            current.state, job.state = "running", "pending"
            if is_activity_step(snapshot, step) and not replay and cursor.offset < cursor.count:
                return snapshot["request"]["activity"]["pacing_ms"]

        changed = await checkpoint(database, claim, now(), complete)
        return JobResult("pending" if changed else "stale", None, None)
    except AdminError as exc:
        return await stop(
            "failed" if exc.status in {400, 401, 403, 404, 409, 422} else "needs-attention",
            "Local operation rejected: " + exc.code,
        )
    except (ValueError, TypeError, KeyError):
        return await stop("failed", "Population input does not match the selected operation")
    except (TimeoutError, OSError):
        return await stop(
            "needs-attention",
            "Operation outcome is uncertain; inspect recorded progress before resuming",
        )
