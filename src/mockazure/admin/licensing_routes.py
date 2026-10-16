from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import ValidationError

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.licensing.schemas import SkuDefinition, SkuInput
from mockazure.licensing.service import inventory, sku_view, upsert_sku
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/licensing")


@router.put("/skus/{sku_id}")
async def put_sku(tenant_id: UUID, sku_id: UUID, request: Request):
    try:
        value = SkuDefinition.model_validate(await read_json(request))
    except (GraphError, ValidationError):
        raise AdminError(422, "InvalidInventory", "Invalid subscribed SKU definition") from None
    async with request.app.state.database.sessions.begin() as session:
        return await upsert_sku(session, tenant_id, SkuInput(sku_id=sku_id, **value.model_dump()))


@router.get("/skus")
async def list_skus(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions() as session:
        if await session.get(Tenant, tenant_id) is None:
            raise AdminError(404, "TenantNotFound", "Tenant not found")
        return {
            "items": [
                sku_view(row).model_dump(mode="json") for row in await inventory(session, tenant_id)
            ]
        }
