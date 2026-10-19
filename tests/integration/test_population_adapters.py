from uuid import uuid4

import pytest

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.population.catalog import get_adapter
from mockazure.population.types import AdapterContext, Mutation, ResourceQuery


async def context(database, settings):
    settings.token_file.write_text("A" * 43)
    tenant = uuid4()
    async with database.sessions.begin() as session:
        session.add(
            Tenant(
                id=tenant, slug="population", display_name="Lab", domain="lab.test", licences=["E5"]
            )
        )
    return AdapterContext(database=database, settings=settings, tenant_id=tenant)


def user(index):
    return Mutation(
        operation="import",
        logical_key=f"user-{index}",
        payload={
            "id": str(uuid4()),
            "displayName": f"User {index}",
            "mailNickname": f"u{index}",
            "userPrincipalName": f"u{index}@lab.test",
            "createdDateTime": "2026-10-01T00:00:00Z",
        },
    )


async def test_directory_additive_replay_paging_and_readback(database, settings):
    ctx = await context(database, settings)
    adapter = get_adapter("directory.users")
    mutations = [user(i) for i in range(4)]
    result = await adapter.apply(ctx, mutations)
    assert result.effects == {"created": 4, "updated": 0, "unchanged": 0}
    assert (await adapter.verify(ctx, result)).status == "succeeded"
    repeat = await adapter.apply(ctx, mutations)
    assert repeat.effects == {"created": 0, "updated": 0, "unchanged": 4}
    first = await adapter.browse(ctx, ResourceQuery(limit=2))
    assert len(first.items) == 2 and first.continuation
    second = await adapter.browse(ctx, ResourceQuery(limit=2, continuation=first.continuation))
    assert len(second.items) == 2 and second.continuation is None
    assert {x.id for x in first.items}.isdisjoint(x.id for x in second.items)
    assert all("verifier" not in str(x) for x in first.items)


async def test_cursor_is_bound_to_tenant_family_and_filters(database, settings):
    ctx = await context(database, settings)
    adapter = get_adapter("directory.users")
    await adapter.apply(ctx, [user(1), user(2)])
    page = await adapter.browse(ctx, ResourceQuery(limit=1))
    with pytest.raises(AdminError):
        await get_adapter("directory.groups").browse(
            ctx, ResourceQuery(limit=1, continuation=page.continuation)
        )
    with pytest.raises(AdminError):
        await adapter.browse(
            ctx,
            ResourceQuery(
                limit=1, continuation=page.continuation, filters={"displayName": "Other"}
            ),
        )


async def test_security_update_requires_version_and_preserves_other_records(database, settings):
    ctx = await context(database, settings)
    adapter = get_adapter("security.alerts_v2")
    a = Mutation(operation="import", logical_key="a", payload={"id": "a", "title": "Original"})
    b = Mutation(operation="import", logical_key="b", payload={"id": "b", "title": "Unrelated"})
    await adapter.apply(ctx, [a, b])
    before = await adapter.inspect(ctx, a)
    changed = a.model_copy(update={"payload": {"id": "a", "title": "Changed"}})
    with pytest.raises(AdminError) as conflict:
        await adapter.apply(ctx, [changed])
    assert conflict.value.status == 409
    await adapter.apply(ctx, [changed.model_copy(update={"expected_version": before.version})])
    with pytest.raises(AdminError):
        await adapter.apply(ctx, [a.model_copy(update={"expected_version": before.version})])
    rows = await adapter.browse(ctx, ResourceQuery(identifier="b"))
    assert rows.items[0].properties["title"] == "Unrelated"


async def test_cross_tenant_id_and_foreign_subject_rejected(database, settings):
    ctx = await context(database, settings)
    with pytest.raises(AdminError):
        await get_adapter("directory.users").apply(
            ctx,
            [
                user(1).model_copy(
                    update={"payload": {**user(1).payload, "userPrincipalName": "u1@foreign.test"}}
                )
            ],
        )
    with pytest.raises(ValueError):
        await get_adapter("security.alerts_v2").apply(
            ctx,
            [
                Mutation(
                    operation="import",
                    logical_key="a",
                    payload={"id": "a", "tenantId": str(uuid4())},
                )
            ],
        )
