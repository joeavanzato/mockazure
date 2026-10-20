"""Bounded relationships for the authenticated lab inspector."""

from sqlalchemy import and_, or_, select

from mockazure.admin.errors import AdminError
from mockazure.population.catalog import get_adapter, registry
from mockazure.population.types import ResourceQuery
from mockazure.workloads.models import WorkloadRelation


async def related(ctx, family, identifier, parent):
    page = await get_adapter(family).browse(
        ctx,
        ResourceQuery(identifier=identifier, parent=parent, limit=1),
    )
    if not page.items:
        raise AdminError(404, "ResourceNotFound", "The selected resource does not exist")
    parts = family.split(".", 1)
    workload = parts[0] if parts[0] in {"sharepoint", "teams", "intune"} else None
    if workload is None or parts[1] == "relationships":
        return {"items": [], "truncated": False}
    kind = parts[1]
    r = WorkloadRelation
    conditions = [
        r.tenant_id == ctx.tenant_id,
        r.workload == workload,
        r.valid_to.is_(None),
        or_(
            and_(r.source_kind == kind, r.source_id == identifier),
            and_(r.target_kind == kind, r.target_id == identifier),
        ),
    ]
    if parent is not None:
        conditions.append(r.scope == parent)
    async with ctx.database.sessions() as session:
        rows = list(
            await session.scalars(
                select(r)
                .where(*conditions)
                .order_by(
                    r.scope,
                    r.source_kind,
                    r.source_id,
                    r.relation,
                    r.target_kind,
                    r.target_id,
                )
                .limit(51)
            )
        )
    items = []
    for row in rows[:50]:
        outgoing = row.source_kind == kind and row.source_id == identifier
        target_kind = row.target_kind if outgoing else row.source_kind
        target = row.target_id if outgoing else row.source_id
        target_family = "directory.users" if target_kind == "user" else workload + "." + target_kind
        if target_family not in registry():
            continue
        items.append(
            {
                "family": target_family,
                "id": target,
                "parent": None if target_kind == "user" else row.scope,
                "relation": row.relation,
                "direction": "outgoing" if outgoing else "incoming",
            }
        )
    return {"items": items, "truncated": len(rows) > 50}
