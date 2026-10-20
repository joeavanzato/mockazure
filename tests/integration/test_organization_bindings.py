from uuid import uuid4

import pytest

from mockazure.population.bindings import EntityBinding, bind, resolve, retire_namespace
from tests.integration.test_population_adapters import context


def binding(tenant, **changes):
    return EntityBinding(
        **{
            "tenant_id": tenant,
            "namespace": "business",
            "logical_key": "device:0",
            "service": "graph",
            "actual_id": "directory-object",
            "parent_id": None,
            "generation": uuid4(),
            **changes,
        }
    )


async def test_bindings_are_tenant_namespace_and_service_scoped(database, settings):
    ctx = await context(database, settings)
    original = binding(ctx.tenant_id)
    async with database.sessions.begin() as session:
        assert await bind(session, original) == original
        assert await bind(session, original) == original
        other = original.model_copy(update={"service": "intune", "actual_id": "intune-record"})
        await bind(session, other)
        assert await resolve(session, ctx.tenant_id, "business", "device:0", "graph") == original
        assert await resolve(session, ctx.tenant_id, "business", "device:0", "intune") == other
        for tenant, namespace, service in (
            (uuid4(), "business", "graph"),
            (ctx.tenant_id, "another", "graph"),
            (ctx.tenant_id, "business", "hunting"),
        ):
            with pytest.raises(ValueError):
                await resolve(session, tenant, namespace, "device:0", service)


async def test_binding_conflict_does_not_retarget_resume(database, settings):
    ctx = await context(database, settings)
    original = binding(ctx.tenant_id)
    async with database.sessions.begin() as session:
        await bind(session, original)
    async with database.sessions.begin() as session:
        with pytest.raises(ValueError, match="conflict"):
            await bind(session, original.model_copy(update={"actual_id": "replacement"}))
    async with database.sessions() as session:
        assert await resolve(session, ctx.tenant_id, "business", "device:0", "graph") == original


async def test_retired_generation_cannot_be_resolved_or_rebound(database, settings):
    ctx = await context(database, settings)
    original = binding(ctx.tenant_id)
    async with database.sessions.begin() as session:
        await bind(session, original)
        generation = await retire_namespace(
            session, ctx.tenant_id, "business", expected_generation=original.generation
        )
        assert generation != original.generation
        with pytest.raises(ValueError, match="stale"):
            await resolve(session, ctx.tenant_id, "business", "device:0", "graph")
        with pytest.raises(ValueError, match="stale"):
            await bind(session, original)
        new = original.model_copy(update={"generation": generation, "actual_id": "new-object"})
        await bind(session, new)
        assert await resolve(session, ctx.tenant_id, "business", "device:0", "graph") == new


async def test_missing_and_foreign_namespace_parent_rejected(database, settings):
    ctx = await context(database, settings)
    root = binding(ctx.tenant_id, logical_key="site:0", actual_id="site-object")
    async with database.sessions.begin() as session:
        await bind(session, root)
        with pytest.raises(ValueError, match="parent"):
            await bind(
                session,
                root.model_copy(
                    update={
                        "namespace": "another",
                        "logical_key": "drive:0",
                        "actual_id": "drive-object",
                        "parent_id": "site-object",
                    }
                ),
            )
        child = root.model_copy(
            update={
                "logical_key": "drive:0",
                "actual_id": "drive-object",
                "parent_id": "site-object",
            }
        )
        assert await bind(session, child) == child


async def test_unknown_tenant_is_not_created_by_binding(database):
    async with database.sessions.begin() as session:
        with pytest.raises(ValueError, match="tenant"):
            await bind(session, binding(uuid4()))


async def test_long_unicode_provider_identifier_is_bindable(database, settings):
    ctx = await context(database, settings)
    identifier = "".join(chr(0x4E00 + (i * 7919) % 20000) for i in range(2048))
    original = binding(ctx.tenant_id, actual_id=identifier)
    async with database.sessions.begin() as session:
        await bind(session, original)
        assert await resolve(session, ctx.tenant_id, "business", "device:0", "graph") == original


async def test_concurrent_binding_conflict_has_one_winner(database, settings):
    import asyncio

    ctx = await context(database, settings)
    original = binding(ctx.tenant_id)

    async def attempt(value):
        try:
            async with database.sessions.begin() as session:
                return await bind(session, value)
        except ValueError:
            return None

    outcomes = await asyncio.gather(
        attempt(original), attempt(original.model_copy(update={"actual_id": "competing"}))
    )
    winners = [value for value in outcomes if value is not None]
    assert len(winners) == 1
    async with database.sessions() as session:
        assert await resolve(session, ctx.tenant_id, "business", "device:0", "graph") == winners[0]


async def test_binding_and_generation_changes_invalidate_preview_state(database, settings):
    from mockazure.population.planner import state_digest

    ctx = await context(database, settings)
    original = binding(ctx.tenant_id)
    async with database.sessions.begin() as session:
        before = await state_digest(session, ctx.tenant_id)
        await bind(session, original)
        after_binding = await state_digest(session, ctx.tenant_id)
        assert after_binding != before
        await bind(session, original)
        assert await state_digest(session, ctx.tenant_id) == after_binding
        await retire_namespace(
            session, ctx.tenant_id, "business", expected_generation=original.generation
        )
        assert await state_digest(session, ctx.tenant_id) != after_binding
