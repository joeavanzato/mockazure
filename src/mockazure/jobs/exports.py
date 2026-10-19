from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.jobs.schemas import ExportSnapshot
from mockazure.tenants.repository import create_tenant
from mockazure.tenants.schemas import TenantCreate, TenantView


async def import_tenant(
    session: AsyncSession,
    snapshot: ExportSnapshot,
    *,
    replacement: TenantCreate | None = None,
    target_tenant_id=None,
) -> TenantView:
    from mockazure.admin.errors import AdminError
    from mockazure.jobs.schemas import CloudExportSnapshot

    if isinstance(snapshot, CloudExportSnapshot):
        from mockazure.cloud.lifecycle import import_cloud
        from mockazure.tenants.repository import get_tenant

        if replacement is not None or target_tenant_id != snapshot.source_tenant_id:
            raise AdminError(
                422, "InvalidCloudRestore", "Cloud snapshots require the original target tenant"
            )
        tenant = await get_tenant(session, target_tenant_id)
        if tenant is None:
            raise AdminError(404, "TenantNotFound", "Restore tenant was not found")
        await import_cloud(session, target_tenant_id, snapshot.cloud)
        return tenant
    if target_tenant_id is not None:
        raise AdminError(422, "InvalidRestoreTarget", "Metadata imports create a new tenant")
    return await create_tenant(session, replacement or snapshot.tenant)


async def export_cloud_snapshot(session: AsyncSession, tenant_id):
    from mockazure.cloud.lifecycle import export_cloud

    return await export_cloud(session, tenant_id)


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
