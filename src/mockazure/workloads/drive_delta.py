"""Drive-root delta over immutable metadata and relationship snapshots."""

from datetime import UTC, datetime

from sqlalchemy import exists, literal, or_, select, union_all

from mockazure.providers.graph.probes import context_url
from mockazure.workloads.cursors import issue_cursor
from mockazure.workloads.drives import drive_tree
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.queries import link, page_state
from mockazure.workloads.repository import scope_row, visible


def version_query(actor, drive, revision, name):
    tree = drive_tree(actor.tenant_id, drive.scope, drive.id, revision, name + "_tree")
    return (
        select(V)
        .where(
            V.tenant_id == actor.tenant_id,
            V.workload == "sharepoint",
            V.scope == drive.scope,
            V.kind == "driveItem",
            V.deleted.is_(False),
            visible(V, revision),
            V.id.in_(select(tree.c.id)),
        )
        .subquery(name)
    )


async def delta_response(request, session, actor, drive):
    now = datetime.now(UTC)
    state, token = await page_state(
        request, session, actor, "driveItem", "drive", drive.scope, delta=True
    )
    options = state["options"]
    if state.get("checkpoint"):
        state["lower"] = state["scopes"][drive.scope]["upper"]
        scope = await scope_row(session, actor.tenant_id, "sharepoint", drive.scope)
        state["scopes"][drive.scope]["upper"] = scope.revision
        state["offset"] = 0
        state["checkpoint"] = False
    lower, upper = state.get("lower"), state["scopes"][drive.scope]["upper"]
    after = version_query(actor, drive, upper, "delta_after")
    changed = select(after.c.id, after.c.payload, literal(False).label("removed"))
    if lower is not None:
        before = version_query(actor, drive, lower, "delta_before")
        edges_changed = exists(
            select(1).where(
                E.tenant_id == actor.tenant_id,
                E.workload == "sharepoint",
                E.scope == drive.scope,
                or_(E.source_id == after.c.id, E.target_id == after.c.id),
                or_(
                    (E.valid_from > lower) & (E.valid_from <= upper),
                    (E.valid_to > lower) & (E.valid_to <= upper),
                ),
            )
        )
        changed = changed.where(
            or_(after.c.valid_from > lower, ~after.c.id.in_(select(before.c.id)), edges_changed)
        )
        removed = select(before.c.id, before.c.payload, literal(True).label("removed")).where(
            ~before.c.id.in_(select(after.c.id))
        )
        events = union_all(changed, removed).subquery("delta_events")
    else:
        events = changed.subquery("delta_events")
    rows = (
        []
        if options["latest"] and token is None
        else (
            await session.execute(
                select(events)
                .order_by(events.c.id)
                .offset(state["offset"])
                .limit(options["top"] + 1)
            )
        )
        .mappings()
        .all()
    )
    values = []
    for row in rows[: options["top"]]:
        value = (
            {"id": row["id"], "deleted": {"state": "deleted"}}
            if row["removed"]
            else {
                key: value
                for key, value in row["payload"].items()
                if options["select"] is None or key in options["select"] or key == "id"
            }
        )
        values.append(value)
    more = len(rows) > options["top"]
    if more:
        state["offset"] += options["top"]
    else:
        state["checkpoint"] = True
        state["offset"] = 0
    handle = await issue_cursor(session, actor, state, now, token)
    return {
        "@odata.context": context_url(request, "driveItem"),
        "value": values,
        "@odata.nextLink" if more else "@odata.deltaLink": link(
            request, request.url.path, handle, delta=not more
        ),
    }
