"""Authenticated lab controls; these are not public Microsoft Graph APIs."""

from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import AwareDatetime, Field
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.admin.profile_routes import ProfileRoute
from mockazure.db.models import Tenant
from mockazure.hunting.contracts import catalog, table_contract
from mockazure.hunting.models import HuntingRecord
from mockazure.hunting.repository import browse_rows, ingest_rows, prune_rows
from mockazure.hunting.schemas import HuntingProvenance
from mockazure.population.types import Input, ResourceQuery
from mockazure.providers.graph.body import read_json

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/hunting", route_class=ProfileRoute)


async def require_tenant(session, tenant_id, *, lock=False):
    statement = select(Tenant.id).where(Tenant.id == tenant_id)
    if lock:
        statement = statement.with_for_update()
    if await session.scalar(statement) is None:
        raise AdminError(404, "TenantNotFound", "Hunting tenant not found")


@router.get("/tables")
async def tables(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions() as session:
        await require_tenant(session, tenant_id)
    return {"tables": [value.model_dump() for value in catalog().values()]}


class Import(Input):
    rows: list[dict] = Field(max_length=10000)
    source_ids: list[str] = Field(max_length=10000)


@router.post("/tables/{table}/import")
async def import_rows(tenant_id: UUID, table: str, request: Request):
    table_contract(table)
    value = Import.model_validate(await read_json(request, limit=8 * 1024 * 1024))
    async with request.app.state.database.sessions.begin() as session:
        count = await ingest_rows(
            session,
            tenant_id,
            table,
            value.rows,
            value.source_ids,
            HuntingProvenance(
                kind="fixture", source_ref="management", generator_version="import-v1"
            ),
        )
    return {"created": count, "unchanged": len(value.rows) - count, "provenance": "fixture"}


@router.get("/tables/{table}/export")
async def export(tenant_id: UUID, table: str, request: Request):
    if not set(request.query_params) <= {"limit", "continuation"}:
        raise ValueError("Unsupported hunting export query")
    query = ResourceQuery(
        limit=int(request.query_params.get("limit", "100")),
        continuation=request.query_params.get("continuation"),
    )
    async with request.app.state.database.sessions() as session:
        await require_tenant(session, tenant_id)
        return await browse_rows(session, tenant_id, table, query)


class Retention(Input):
    before: AwareDatetime


@router.post("/retention")
async def retention(tenant_id: UUID, value: Retention, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        await require_tenant(session, tenant_id)
        count = await prune_rows(session, tenant_id, value.before)
    return {"removed": count}


class Reset(Input):
    confirm_tenant_id: UUID


@router.post("/reset")
async def reset(tenant_id: UUID, value: Reset, request: Request):
    if value.confirm_tenant_id != tenant_id:
        raise ValueError("Reset confirmation does not match this tenant")
    async with request.app.state.database.sessions.begin() as session:
        await require_tenant(session, tenant_id, lock=True)
        result = await session.execute(
            delete(HuntingRecord).where(
                HuntingRecord.tenant_id == tenant_id,
            )
        )
    return {"removed": result.rowcount}
