"""Graph v1.0 licence operations, backed by tenant inventory and assignments."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from fastapi import APIRouter, Request
from pydantic import ValidationError
from sqlalchemy import select

from mockazure.authorization.roles import effective_roles
from mockazure.identity.models import User
from mockazure.licensing.schemas import LicenceAssignment
from mockazure.licensing.service import assign_licences, assignment_rows, inventory
from mockazure.providers.graph.body import invalid, read_json
from mockazure.providers.graph.directory import get_object
from mockazure.providers.graph.permissions import deny
from mockazure.providers.graph.probes import context_url, principal
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.providers.graph.serialization import selected_fields

router = APIRouter(prefix="/v1.0")
SKU_READ = frozenset(
    {
        "LicenseAssignment.Read.All",
        "Directory.Read.All",
        "Directory.ReadWrite.All",
        "Organization.Read.All",
        "Organization.ReadWrite.All",
    }
)
DETAIL_READ = frozenset(
    {
        "LicenseAssignment.Read.All",
        "Directory.Read.All",
        "Directory.ReadWrite.All",
        "User.Read.All",
        "User.ReadWrite.All",
    }
)
ASSIGN = frozenset(
    {"LicenseAssignment.ReadWrite.All", "Directory.ReadWrite.All", "User.ReadWrite.All"}
)
SKU_ROLES = frozenset(
    {
        "Global Administrator",
        "Global Reader",
        "Directory Readers",
        "Dynamics 365 Business Central Administrator",
    }
)
DETAIL_ROLES = frozenset(
    {
        "Global Administrator",
        "Guest Inviter",
        "Directory Readers",
        "Directory Writers",
        "License Administrator",
        "User Administrator",
    }
)
ASSIGN_ROLES = frozenset(
    {"Global Administrator", "Directory Writers", "License Administrator", "User Administrator"}
)


async def authorize_licensing(session, context, operation, target_id=None):
    from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity

    try:
        await require_current_identity(context, session)
    except AuthorizationDenied:
        deny()
    if operation == "details" and context.token_kind != "delegated":
        deny()
    allowed = {"skus": SKU_READ, "details": DETAIL_READ, "assign": ASSIGN}[operation]
    if operation == "details" and target_id is not None and target_id == context.subject_id:
        allowed = allowed | {"User.Read"}
    if not context.permissions & allowed:
        deny()
    if context.token_kind == "delegated":
        roles = set(await effective_roles(session, context.tenant_id, context.subject_id))
        if (
            not roles
            & {"skus": SKU_ROLES, "details": DETAIL_ROLES, "assign": ASSIGN_ROLES}[operation]
        ):
            deny()


def sku_payload(row):
    return {
        "id": str(row.id),
        "skuId": str(row.sku_id),
        "skuPartNumber": row.sku_part_number,
        "capabilityStatus": row.status,
        "consumedUnits": row.consumed_units,
        "appliesTo": "User",
        "prepaidUnits": {
            "enabled": row.enabled,
            "suspended": row.suspended,
            "warning": row.warning,
        },
        "servicePlans": row.service_plans,
    }


def project(request, kind, payload):
    validate_payload("v1.0", kind, payload)
    chosen = selected_fields(request)
    if chosen is not None and not set(chosen) <= payload.keys():
        from mockazure.providers.graph.query.contracts import unsupported

        unsupported("Selected field is not supported")
    selected = set(chosen) if chosen else set(payload)
    return {
        "@odata.type": "#microsoft.graph." + kind,
        **{k: v for k, v in payload.items() if k in selected | {"id"}},
    }


@router.get("/subscribedSkus")
async def skus(request: Request):
    async with request.app.state.database.sessions() as session:
        context = await principal(request, session)
        await authorize_licensing(session, context, "skus")
        rows = await inventory(session, context.tenant_id)
        # Validate select even for an empty inventory.
        fields = selected_fields(request)
        allowed = {
            "id",
            "skuId",
            "skuPartNumber",
            "capabilityStatus",
            "consumedUnits",
            "appliesTo",
            "prepaidUnits",
            "servicePlans",
        }
        if fields is not None and not set(fields) <= allowed:
            from mockazure.providers.graph.query.contracts import unsupported

            unsupported("Selected field is not supported")
        return {
            "@odata.context": context_url(request, "subscribedSkus"),
            "value": [
                project(request, "subscribedSku", sku_payload(row))
                for row in sorted(rows, key=lambda row: row.id)
            ],
        }


@router.get("/me/licenseDetails")
@router.get("/users/{identifier}/licenseDetails")
async def details(request: Request, identifier: str | None = None):
    async with request.app.state.database.sessions() as session:
        context = await principal(request, session)
        own = (
            context.subject_id
            if identifier is None or identifier == str(context.subject_id)
            else None
        )
        if own is None and context.subject_id:
            own = await session.scalar(
                select(User.id).where(
                    User.tenant_id == context.tenant_id,
                    User.id == context.subject_id,
                    User.upn == identifier.lower(),
                )
            )
        await authorize_licensing(session, context, "details", own)
        user = await get_object(
            session, context.tenant_id, "user", identifier or str(context.subject_id)
        )
        await authorize_licensing(session, context, "details", user.id)
        fields = selected_fields(request)
        if fields is not None and not set(fields) <= {
            "id",
            "skuId",
            "skuPartNumber",
            "servicePlans",
        }:
            from mockazure.providers.graph.query.contracts import unsupported

            unsupported("Selected field is not supported")
        values = []
        for assignment, sku in await assignment_rows(session, context.tenant_id, user.id):
            payload = {
                "id": str(uuid5(user.id, "license:" + str(sku.sku_id))),
                "skuId": str(sku.sku_id),
                "skuPartNumber": sku.sku_part_number,
                "servicePlans": [
                    {
                        **plan,
                        "provisioningStatus": "Disabled"
                        if plan["servicePlanId"] in assignment.disabled_plans
                        else plan["provisioningStatus"],
                    }
                    for plan in sku.service_plans
                ],
            }
            values.append(project(request, "licenseDetails", payload))
        return {
            "@odata.context": context_url(request, "users('" + str(user.id) + "')/licenseDetails"),
            "value": sorted(values, key=lambda v: v["id"]),
        }


@router.post("/users/{identifier}/assignLicense")
@router.post("/users/{identifier}/microsoft.graph.assignLicense")
async def assign_license(identifier: str, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        context = await principal(request, session)
        await authorize_licensing(session, context, "assign")
        if request.query_params:
            invalid("assignLicense does not support query options")
        try:
            command = LicenceAssignment.model_validate(await read_json(request))
        except ValidationError:
            invalid("Invalid licence assignment")
        user = await get_object(session, context.tenant_id, "user", identifier)
        result = await assign_licences(
            session, context, user.id, command, datetime.now(UTC), UUID(request.state.request_id)
        )
        return {
            "@odata.context": context_url(request, "users/$entity"),
            "@odata.type": "#microsoft.graph.user",
            **result.properties,
        }
