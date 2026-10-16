from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from mockazure.admin.errors import AdminError
from mockazure.db.migrations import schema_is_current
from mockazure.identity.keys import SigningKeyStore, identity_ready, read_wrapping_key
from mockazure.tenants.repository import create_tenant, get_tenant, list_tenants
from mockazure.tenants.schemas import TenantCreate, TenantPage, TenantView

router = APIRouter(prefix="/_mock")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    if not await schema_is_current(request.app.state.database):
        raise AdminError(503, "NotReady", "Database or schema is not ready")
    if not await identity_ready(request.app.state.settings, request.app.state.database):
        raise AdminError(503, "IdentityNotReady", "Identity wrapping material is unavailable")
    return {"status": "ready"}


@router.get("/capabilities")
async def capabilities():
    return {
        "milestone": 1,
        "provider_operations": [],
        "admin_operations": [
            "tenant.create",
            "tenant.get",
            "tenant.list",
            "tenant.export",
            "tenant.import",
            "job.get",
            "job.result",
        ],
    }


@router.post("/tenants", status_code=201, response_model=TenantView)
async def tenant_create(data: TenantCreate, request: Request, response: Response):
    async with request.app.state.database.sessions.begin() as session:
        tenant = await create_tenant(session, data)
    response.headers["Location"] = (
        str(request.app.state.settings.admin_base_url).rstrip("/") + f"/_mock/tenants/{tenant.id}"
    )
    return tenant


@router.get("/tenants", response_model=TenantPage)
async def tenant_list(
    request: Request, after: UUID | None = None, limit: int = Query(100, ge=1, le=500)
):
    async with request.app.state.database.sessions() as session:
        rows = await list_tenants(session, after=after, limit=limit + 1)
    return TenantPage(
        items=rows[:limit], next_after=rows[limit - 1].id if len(rows) > limit else None
    )


@router.get("/tenants/{tenant_id}", response_model=TenantView)
async def tenant_get(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions() as session:
        tenant = await get_tenant(session, tenant_id)
    if tenant is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")
    return tenant


@router.post("/identity/keys/rotate")
async def rotate_identity_keys(request: Request):
    settings = request.app.state.settings
    try:
        wrapping = read_wrapping_key(settings.data_dir)
        async with request.app.state.database.sessions.begin() as session:
            key = await SigningKeyStore(session, wrapping, settings.endpoints).rotate(
                datetime.now(UTC)
            )
            return {"kid": key.kid}
    except ValueError:
        raise AdminError(
            503, "IdentityNotReady", "Identity wrapping material is unavailable"
        ) from None
