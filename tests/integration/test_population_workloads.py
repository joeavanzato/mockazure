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


async def test_workload_metadata_edit_preserves_attached_content(database, settings):
    from sqlalchemy import select

    from mockazure.population.types import AdapterContext
    from mockazure.workloads.models import WorkloadVersion
    from tests.integration.graph_support import graph_client
    from tests.integration.site_drive_support import seed_site

    async with graph_client(database, settings) as (client, tenant, _, user):
        fixture = await seed_site(client, database, tenant, user)
        ctx = AdapterContext(database, settings, tenant)
        adapter = get_adapter("sharepoint.driveItem")
        row = (
            await adapter.browse(
                ctx, ResourceQuery(parent=fixture["site"], identifier=fixture["item"])
            )
        ).items[0]
        async with database.sessions() as session:
            original = await session.scalar(
                select(WorkloadVersion.content_id).where(
                    WorkloadVersion.tenant_id == tenant,
                    WorkloadVersion.id == fixture["item"],
                    WorkloadVersion.valid_to.is_(None),
                )
            )
        assert original
        await adapter.apply(
            ctx,
            [
                Mutation(
                    operation="import",
                    logical_key="rename",
                    parent=fixture["site"],
                    payload={**row.properties, "name": "renamed.txt"},
                    expected_version=row.version,
                )
            ],
        )
        async with database.sessions() as session:
            actual = await session.scalar(
                select(WorkloadVersion.content_id).where(
                    WorkloadVersion.tenant_id == tenant,
                    WorkloadVersion.id == fixture["item"],
                    WorkloadVersion.valid_to.is_(None),
                )
            )
        assert actual == original


async def test_linked_import_updates_relationship_when_payload_is_unchanged(database, settings):
    from sqlalchemy import func, select

    from mockazure.workloads.models import WorkloadRelation

    ctx = await context(database, settings)
    site = "lab.test," + str(uuid4()) + "," + str(uuid4())
    drive = str(uuid4())
    adapter = get_adapter("sharepoint.site")
    payload = {**TIMES, "id": site, "displayName": "Site"}
    first = await adapter.apply(
        ctx, [Mutation(operation="import", logical_key="site", parent=site, payload=payload)]
    )
    await get_adapter("sharepoint.drive").apply(
        ctx,
        [
            Mutation(
                operation="import",
                logical_key="drive",
                parent=site,
                payload={**TIMES, "id": drive, "name": "Docs"},
            )
        ],
    )
    linked = Mutation(
        operation="import-linked",
        logical_key="site",
        parent=site,
        expected_version=first.items[0].version,
        payload={
            "payload": payload,
            "edges": [
                {
                    "source": {"kind": "site", "id": site},
                    "relation": "drives",
                    "target": {"kind": "drive", "id": drive},
                }
            ],
        },
    )
    result = await adapter.apply(ctx, [linked])
    assert result.effects["updated"] == 1
    async with database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WorkloadRelation)
                .where(
                    WorkloadRelation.tenant_id == ctx.tenant_id, WorkloadRelation.valid_to.is_(None)
                )
            )
            == 1
        )
    repeated = await adapter.apply(
        ctx, [linked.model_copy(update={"expected_version": result.items[0].version})]
    )
    assert repeated.effects["unchanged"] == 1


async def test_large_workload_edit_rejects_before_whole_scope_materialization(database, settings):
    from sqlalchemy import event, text

    from mockazure.admin.errors import AdminError

    ctx = await context(database, settings)
    adapter = get_adapter("sharepoint.site")
    site = "lab.test," + str(uuid4()) + "," + str(uuid4())
    value = Mutation(
        operation="import",
        logical_key="site",
        parent=site,
        payload={**TIMES, "id": site, "displayName": "Site"},
    )
    await adapter.apply(ctx, [value])
    async with database.sessions.begin() as session:
        await session.execute(
            text(
                "INSERT INTO workload_versions (tenant_id, workload, scope, kind, id, "
                "valid_from, deleted, payload) SELECT :tenant, 'sharepoint', :scope, "
                "'driveItem', 'bulk-' || n, 1, false, jsonb_build_object('id', 'bulk-' || n, "
                "'name', 'Folder ' || n, 'folder', jsonb_build_object('childCount',0)) "
                "FROM generate_series(1,10000) n"
            ),
            {"tenant": ctx.tenant_id, "scope": site},
        )
    inspection = await adapter.inspect(ctx, value)
    queries = []

    def observe(connection, cursor, statement, parameters, context, many):
        queries.append(statement)

    event.listen(database.engine.sync_engine, "before_cursor_execute", observe)
    try:
        with pytest.raises(AdminError) as error:
            await adapter.apply(
                ctx,
                [
                    value.model_copy(
                        update={
                            "expected_version": inspection.version,
                            "payload": {**value.payload, "displayName": "Changed"},
                        }
                    )
                ],
            )
        assert error.value.status == 413
        assert not any(
            "SELECT workload_versions.kind, workload_versions.id, workload_versions.name" in q
            for q in queries
        )
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", observe)
    assert (await adapter.browse(ctx, ResourceQuery(parent=site, identifier=site))).items[
        0
    ].properties["displayName"] == "Site"
