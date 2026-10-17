from uuid import UUID

from fastapi import APIRouter, Request

from mockazure.admin.errors import AdminError
from mockazure.admin.evidence_routes import tenant_session
from mockazure.collections.repository import import_records
from mockazure.providers.graph.body import read_json

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/collections")


@router.post("/{family}/import")
async def import_collection(tenant_id: UUID, family: str, request: Request):
    body = await read_json(request, limit=8 * 1024 * 1024)
    if set(body) != {"records"}:
        raise AdminError(422, "InvalidCollection", "Expected records only")
    async with tenant_session(request, tenant_id) as session:
        try:
            return await import_records(session, tenant_id, family, body["records"])
        except (ValueError, TypeError, OverflowError):
            raise AdminError(
                422, "InvalidCollection", "Invalid collection records or tenant binding"
            ) from None
