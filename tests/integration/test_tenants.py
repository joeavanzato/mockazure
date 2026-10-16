import asyncio

import pytest

from mockazure.db.engine import close_database, create_database
from mockazure.tenants.repository import TenantConflict, create_tenant, get_tenant, list_tenants
from mockazure.tenants.schemas import TenantCreate


async def test_persistence_and_uuid_keyset(database, settings, tenant_payload):
    data = TenantCreate(**tenant_payload)
    async with database.sessions.begin() as session:
        first = await create_tenant(session, data)
        second = await create_tenant(
            session, data.model_copy(update={"slug": "fabrikam", "domain": "fabrikam.test"})
        )
    assert first.id != second.id
    assert first.revision == 1
    await close_database(database)
    reopened = create_database(settings)
    try:
        async with reopened.sessions() as session:
            assert (await get_tenant(session, first.id)).domain == "contoso.test"
            page = await list_tenants(session, after=None, limit=1)
            assert [item.id for item in page] == [min(first.id, second.id)]
            page = await list_tenants(session, after=page[0].id, limit=1)
            assert [item.id for item in page] == [max(first.id, second.id)]
    finally:
        await close_database(reopened)


async def test_concurrent_duplicate_is_conflict(database, tenant_payload):
    async def insert():
        try:
            async with database.sessions.begin() as session:
                return await create_tenant(session, TenantCreate(**tenant_payload))
        except TenantConflict:
            return "conflict"

    outcomes = await asyncio.gather(insert(), insert())
    assert outcomes.count("conflict") == 1


async def test_domain_is_unique(database, tenant_payload):
    async with database.sessions.begin() as session:
        await create_tenant(session, TenantCreate(**tenant_payload))
    with pytest.raises(TenantConflict):
        async with database.sessions.begin() as session:
            await create_tenant(session, TenantCreate(**{**tenant_payload, "slug": "other"}))
