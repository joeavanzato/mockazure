from uuid import uuid4

import pytest

from mockazure.population.catalog import get_adapter
from mockazure.population.types import Mutation, ResourceQuery
from tests.integration.test_population_adapters import context

TIMES = {"createdDateTime": "2026-10-01T00:00:00Z", "lastModifiedDateTime": "2026-10-01T00:00:00Z"}


async def test_site_drive_relationship_survives_resource_update(database, settings):
    from mockazure.workloads.imports import WorkloadImport
    from mockazure.workloads.repository import import_workload

    ctx = await context(database, settings)
    site, drive = "lab.test," + str(uuid4()) + "," + str(uuid4()), str(uuid4())
    async with database.sessions.begin() as session:
        await import_workload(
            session,
            ctx.tenant_id,
            "sharepoint",
            WorkloadImport.model_validate(
                {
                    "scope": site,
                    "records": [
                        {
                            "kind": "site",
                            "id": site,
                            "payload": {**TIMES, "id": site, "displayName": "Site"},
                        },
                        {
                            "kind": "drive",
                            "id": drive,
                            "payload": {**TIMES, "id": drive, "name": "Docs"},
                        },
                    ],
                    "edges": [
                        {
                            "source": {"kind": "site", "id": site},
                            "relation": "drives",
                            "target": {"kind": "drive", "id": drive},
                        }
                    ],
                }
            ),
        )
    adapter = get_adapter("sharepoint.site")
    mutation = Mutation(
        operation="import",
        logical_key="site",
        parent=site,
        payload={**TIMES, "id": site, "displayName": "Renamed"},
    )
    inspected = await adapter.inspect(ctx, mutation)
    await adapter.apply(ctx, [mutation.model_copy(update={"expected_version": inspected.version})])
    from sqlalchemy import func, select

    from mockazure.workloads.models import WorkloadRelation

    async with database.sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(WorkloadRelation)
            .where(WorkloadRelation.tenant_id == ctx.tenant_id, WorkloadRelation.valid_to.is_(None))
        )
        assert count == 1
    rows = await get_adapter("sharepoint.drive").browse(ctx, ResourceQuery(parent=site))
    assert rows.items[0].properties["name"] == "Docs"


async def test_workload_import_preserves_contract_and_parent_scope(database, settings):
    ctx = await context(database, settings)
    site = "lab.test," + str(uuid4()) + "," + str(uuid4())
    adapter = get_adapter("sharepoint.site")
    value = Mutation(
        operation="import",
        logical_key="site",
        parent=site,
        payload={**TIMES, "id": site, "displayName": "Site"},
    )
    await adapter.apply(ctx, [value])
    with pytest.raises(ValueError):
        adapter.validate(value.model_copy(update={"payload": {"id": site, "invented": 1}}))
    assert (await adapter.browse(ctx, ResourceQuery(parent="other"))).items == []
