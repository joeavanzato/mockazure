"""Current typed fixture export/reset; grants remain explicit separate authority."""

import json
from uuid import uuid4

from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.content.repository import release_content
from mockazure.workloads.models import WorkloadGrant as G
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import pin_owner, scope_row


async def export_records(session, tenant, workload, scope):
    state = await scope_row(session, tenant, workload, scope, lock="read")
    if state is None:
        raise AdminError(404, "ScopeNotFound", "Workload scope was not found")
    yield {"type": "scope", "value": {"scope": scope, "expected_revision": state.revision}}
    records = await session.stream_scalars(
        select(V)
        .where(
            V.tenant_id == tenant,
            V.workload == workload,
            V.scope == scope,
            V.valid_to.is_(None),
            V.deleted.is_(False),
        )
        .order_by(V.kind, V.id)
        .execution_options(yield_per=1)
    )
    async for row in records:
        yield {
            "type": "record",
            "value": {
                "kind": row.kind,
                "id": row.id,
                "payload": row.payload,
                "content_id": str(row.content_id) if row.content_id else None,
            },
        }
    edges = await session.stream_scalars(
        select(E)
        .where(
            E.tenant_id == tenant, E.workload == workload, E.scope == scope, E.valid_to.is_(None)
        )
        .order_by(E.source_kind, E.source_id, E.relation, E.target_id)
        .execution_options(yield_per=1)
    )
    async for row in edges:
        yield {
            "type": "edge",
            "value": {
                "source": {"kind": row.source_kind, "id": row.source_id},
                "relation": row.relation,
                "target": {"kind": row.target_kind, "id": row.target_id},
            },
        }


async def export_workload(session, tenant, workload, scope):
    result = {"records": [], "edges": [], "tombstones": []}
    size, count = 256, 0
    async for row in export_records(session, tenant, workload, scope):
        size += len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
        count += row["type"] != "scope"
        if count > 1000 or size > 8 * 1024 * 1024:
            raise AdminError(
                413, "UseStreamingExport", "Use format=ndjson for bounded record streaming"
            )
        if row["type"] == "scope":
            result.update(row["value"])
        else:
            result[row["type"] + "s"].append(row["value"])
    return result


async def reset_workload(session, tenant, workload, scope, expected_revision):
    state = await scope_row(session, tenant, workload, scope, lock=True)
    if state is None:
        raise AdminError(404, "ScopeNotFound", "Workload scope was not found")
    if state.revision != expected_revision:
        raise AdminError(409, "WorkloadRevisionConflict", "Workload revision changed")
    records = await session.stream_scalars(
        select(V)
        .where(V.tenant_id == tenant, V.workload == workload, V.scope == scope)
        .execution_options(yield_per=1)
    )
    async for row in records:
        if row.content_id:
            await release_content(
                session,
                tenant,
                "workload-version",
                pin_owner(workload, scope, row.kind, row.id, row.valid_from),
            )
    for model in (G, E, V):
        await session.execute(
            delete(model).where(
                model.tenant_id == tenant, model.workload == workload, model.scope == scope
            )
        )
    # Keep invalidated handles until expiry so resumption reports410 after fixture restoration.
    state.revision, state.generation = 0, uuid4()
    state.grant_revision += 1
    await session.flush()
    return {"scope": scope, "revision": 0}
