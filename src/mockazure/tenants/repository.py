from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.db.models import Tenant
from mockazure.tenants.schemas import TenantCreate, TenantView


class TenantConflict(Exception):
    """A tenant already owns the requested slug or domain."""


async def create_tenant(session: AsyncSession, data: TenantCreate) -> TenantView:
    row = Tenant(**data.model_dump())
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise TenantConflict("Tenant slug or domain already exists") from None
        raise
    return TenantView.model_validate(row)


async def get_tenant(session: AsyncSession, tenant_id: UUID) -> TenantView | None:
    row = await session.get(Tenant, tenant_id)
    return TenantView.model_validate(row) if row else None


async def list_tenants(
    session: AsyncSession, *, after: UUID | None, limit: int
) -> list[TenantView]:
    if not 1 <= limit <= 501:
        raise ValueError("tenant page limit is out of bounds")
    query = select(Tenant).order_by(Tenant.id).limit(limit)
    if after is not None:
        query = query.where(Tenant.id > after)
    return [TenantView.model_validate(row) for row in (await session.scalars(query)).all()]
