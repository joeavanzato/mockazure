from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.jobs.schemas import ExportSnapshot
from mockazure.tenants.repository import create_tenant
from mockazure.tenants.schemas import TenantCreate, TenantView


async def import_tenant(
    session: AsyncSession, snapshot: ExportSnapshot, *, replacement: TenantCreate | None = None
) -> TenantView:
    return await create_tenant(session, replacement or snapshot.tenant)
