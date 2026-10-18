from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.jobs.schemas import ExportSnapshot
from mockazure.tenants.repository import create_tenant
from mockazure.tenants.schemas import TenantCreate, TenantView


async def import_tenant(
    session: AsyncSession, snapshot: ExportSnapshot, *, replacement: TenantCreate | None = None
) -> TenantView:
    return await create_tenant(session, replacement or snapshot.tenant)


async def export_content_manifest(session: AsyncSession, tenant_id):
    """Companion artifact; preserve the historical configuration-only schema1 export."""
    from mockazure.admin.mail_routes import tenant_required
    from mockazure.content.repository import export_manifest

    await tenant_required(session, tenant_id)
    return {"content": await export_manifest(session, tenant_id)}


async def validate_content_restore(session: AsyncSession, tenant_id, manifest, mapping):
    from mockazure.admin.mail_routes import tenant_required
    from mockazure.content.repository import validate_manifest

    await tenant_required(session, tenant_id)
    await validate_manifest(session, tenant_id, manifest, mapping)
    return {"validated": len(manifest)}
