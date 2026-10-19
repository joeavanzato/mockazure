"""Bound whole-scope validation before the existing workload importer allocates it."""

from sqlalchemy import func, select

from mockazure.admin.errors import AdminError
from mockazure.workloads.models import WorkloadRelation, WorkloadVersion
from mockazure.workloads.repository import import_workload, scope_row


async def bounded_import_workload(session, tenant, workload, batch):
    await scope_row(session, tenant, workload, batch.scope, create=True, lock=True)
    for model, limit in ((WorkloadVersion, 10000), (WorkloadRelation, 20000)):
        conditions = [
            model.tenant_id == tenant,
            model.workload == workload,
            model.scope == batch.scope,
            model.valid_to.is_(None),
        ]
        if model is WorkloadVersion:
            conditions.append(model.deleted.is_(False))
        count = await session.scalar(select(func.count()).select_from(model).where(*conditions))
        incoming = len(batch.records) if model is WorkloadVersion else len(batch.edges)
        if count + incoming > limit:
            raise AdminError(
                413,
                "WorkloadEditLimit",
                "Console workload validation is limited to 10,000 records and 20,000 "
                "relationships including this batch; use the explicit bulk workload "
                "interface for larger scopes",
            )
    return await import_workload(session, tenant, workload, batch)
