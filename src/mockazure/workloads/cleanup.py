"""Bounded workload history cleanup; scope locks serialize cursor publication."""

from sqlalchemy import BigInteger, case, exists, func, or_, select

from mockazure.content.repository import release_content
from mockazure.workloads.models import WorkloadCursor as C
from mockazure.workloads.models import WorkloadDownload as D
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadScope as S
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import pin_owner


async def cleanup_workloads(session, tenant, now, limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be1..1000")
    count = 0
    for model in (C, D):
        if count == limit:
            break
        rows = list(
            await session.scalars(
                select(model)
                .where(model.tenant_id == tenant, model.expires_at <= now)
                .order_by(model.token_hash)
                .limit(limit - count)
                .with_for_update(skip_locked=True)
            )
        )
        for row in rows:
            if model is D:
                await release_content(session, tenant, "workload-download", row.token_hash)
            await session.delete(row)
        count += len(rows)

    def floor(scope):
        upper = C.state["scopes"][scope]["upper"].as_string()
        needed = case(
            (C.state["checkpoint"].as_boolean().is_(True), upper),
            (C.state["lower"].as_string().is_not(None), C.state["lower"].as_string()),
            else_=upper,
        ).cast(BigInteger)
        return (
            select(func.min(needed))
            .where(
                C.tenant_id == tenant,
                C.workload == S.workload,
                C.expires_at > now,
                C.state["scopes"].has_key(scope),
            )
            .correlate(S)
            .scalar_subquery()
        )

    cutoff = func.coalesce(floor(S.scope), S.revision + 1)

    def eligible(model, boundary):
        return or_(
            model.valid_to <= boundary,
            (model.deleted.is_(True) & (model.valid_from <= boundary)) if model is V else False,
        )

    candidates = [
        exists(
            select(1).where(
                model.tenant_id == S.tenant_id,
                model.workload == S.workload,
                model.scope == S.scope,
                eligible(model, cutoff),
            )
        )
        for model in (V, E)
    ]
    if count < limit:
        scopes = list(
            await session.scalars(
                select(S)
                .where(S.tenant_id == tenant, or_(*candidates))
                .order_by(S.workload, S.scope)
                .limit(limit - count)
                .with_for_update(skip_locked=True)
            )
        )
        for scope in scopes:
            # Recompute after the lock; every cursor issuer keeps a shared scope lease.
            boundary = await session.scalar(
                select(cutoff).where(
                    S.tenant_id == tenant, S.workload == scope.workload, S.scope == scope.scope
                )
            )
            for model in (V, E):
                rows = list(
                    await session.scalars(
                        select(model)
                        .where(
                            model.tenant_id == tenant,
                            model.workload == scope.workload,
                            model.scope == scope.scope,
                            eligible(model, boundary),
                        )
                        .order_by(model.valid_from)
                        .limit(limit - count)
                    )
                )
                for row in rows:
                    if model is V and row.content_id:
                        await release_content(
                            session,
                            tenant,
                            "workload-version",
                            pin_owner(row.workload, row.scope, row.kind, row.id, row.valid_from),
                        )
                    await session.delete(row)
                count += len(rows)
                if count == limit:
                    break
            if count == limit:
                break
    await session.flush()
    return {"deleted": count, "remaining": count == limit}
