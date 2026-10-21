"""Authenticated service browsing and explicit preview/apply receipts."""

import json
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field

from mockazure.admin.errors import AdminError
from mockazure.console.workbench import adapter_for as get_adapter
from mockazure.population.catalog import list_families
from mockazure.population.tokens import decode, fingerprint, sign
from mockazure.population.types import AdapterContext, Input, Mutation, ResourceQuery
from mockazure.providers.graph.body import read_json

router = APIRouter(prefix="/_mock")


def adapter_for(family):
    try:
        return get_adapter(family)
    except ValueError:
        raise AdminError(404, "UnknownFamily", "Resource family is not available") from None


def context(request, tenant_id):
    return AdapterContext(request.app.state.database, request.app.state.settings, tenant_id)


@router.get("/resources/catalog")
async def catalog():
    return {"families": list_families()}


@router.get("/tenants/{tenant_id}/resources/{family}")
async def resources(
    tenant_id: UUID,
    family: str,
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    continuation: str | None = Query(None, max_length=8192),
    parent: str | None = Query(None, max_length=2048),
    identifier: str | None = Query(None, max_length=2048),
    filters: str = Query("{}", max_length=4096),
):
    try:
        query = ResourceQuery(
            limit=limit,
            continuation=continuation,
            parent=parent,
            identifier=identifier,
            filters=json.loads(filters),
        )
        return await adapter_for(family).browse(context(request, tenant_id), query)
    except ValueError:
        raise AdminError(422, "InvalidResourceQuery", "Resource query is invalid") from None


@router.get("/tenants/{tenant_id}/resources/{family}/related")
async def related_resources(
    tenant_id: UUID,
    family: str,
    request: Request,
    identifier: str = Query(..., min_length=1, max_length=2048),
    parent: str | None = Query(None, max_length=2048),
):
    from mockazure.population.navigation import related

    adapter_for(family)
    return await related(context(request, tenant_id), family, identifier, parent)


@router.post("/tenants/{tenant_id}/resources/{family}/preview")
async def preview(tenant_id: UUID, family: str, request: Request):
    adapter, ctx = adapter_for(family), context(request, tenant_id)
    try:
        mutation = Mutation.model_validate(await read_json(request, limit=8 * 1024 * 1024))
        adapter.validate(mutation)
        inspection = await adapter.inspect(ctx, mutation)
    except (ValueError, TypeError):
        raise AdminError(
            422, "InvalidResource", "Resource does not match the operation contract"
        ) from None
    prepared = mutation.model_copy(update={"expected_version": inspection.version or "absent"})
    operation = next(o for o in adapter.describe().operations if o.key == mutation.operation)
    base = getattr(adapter, "base", adapter)
    effect = (
        "update"
        if operation.mode == "service" and operation.method == "PATCH"
        else "replace-record"
        if inspection.exists and getattr(base, "replace_allowed", False)
        else "verify-existing"
        if inspection.exists
        else "create"
    )
    receipt = sign(
        ctx.settings,
        "resource-preview",
        {
            "tenant": str(tenant_id),
            "family": family,
            "digest": fingerprint(prepared),
            "effect": effect,
            "management": [
                request.state.management_identity.method,
                str(request.state.management_identity.session_id),
            ],
        },
    )
    before = None if inspection.exists else {}
    if inspection.exists and inspection.actual_id:
        try:
            page = await adapter.browse(
                ctx, ResourceQuery(identifier=inspection.actual_id, parent=mutation.parent)
            )
            before = page.items[0].properties if page.items else None
        except (ValueError, AdminError):
            pass
    proposed = mutation.payload.get("body", {}) if operation.mode == "service" else mutation.payload
    changes = []
    if before is not None:
        keys = set(proposed) | (set(before) if effect == "replace-record" else set())
        for key in sorted(keys):
            if before.get(key) != proposed.get(key) or (key in before) != (key in proposed):
                changes.append(
                    {
                        "field": key,
                        "before": before.get(key),
                        "after": proposed.get(key),
                        "kind": "removed"
                        if key not in proposed
                        else "added"
                        if key not in before
                        else "changed",
                    }
                )
    return {
        "changes": changes,
        "warnings": inspection.conflicts,
        "mutation": prepared,
        "receipt": receipt,
        "effect": effect,
        "affected_ids": [inspection.actual_id or prepared.payload.get("id")],
        "inspection": inspection,
    }


class ApplyRequest(Input):
    mutation: Mutation
    receipt: str = Field(max_length=8192)
    allow_replace: bool = False


@router.post("/tenants/{tenant_id}/resources/{family}/apply")
async def apply(tenant_id: UUID, family: str, request: Request):
    ctx, adapter = context(request, tenant_id), adapter_for(family)
    try:
        value = ApplyRequest.model_validate(await read_json(request, limit=8 * 1024 * 1024))
        receipt = decode(ctx.settings, "resource-preview", value.receipt)
        if (receipt.get("tenant"), receipt.get("family"), receipt.get("digest")) != (
            str(tenant_id),
            family,
            fingerprint(value.mutation),
        ):
            raise AdminError(409, "PreviewChanged", "Mutation does not match its preview")
        identity = request.state.management_identity
        if receipt.get("management") != [identity.method, str(identity.session_id)]:
            raise AdminError(
                409, "PreviewContextChanged", "Preview belongs to another administration session"
            )
        if receipt.get("effect") == "replace-record" and not value.allow_replace:
            raise AdminError(409, "ReplacementRequired", "Explicit record replacement is required")
        return await adapter.apply(ctx, [value.mutation])
    except (ValueError, TypeError):
        raise AdminError(
            422, "InvalidResource", "Resource does not match the operation contract"
        ) from None
