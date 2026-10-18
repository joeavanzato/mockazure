"""Explicit Storage provider operations with current ARM permissions."""

import copy
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from mockazure.identity.keys import read_wrapping_key
from mockazure.providers.arm.authorization import grant_query, readable_scopes, require_action
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import inventory_view, resolve_scope
from mockazure.providers.arm.inventory_routes import request_scope
from mockazure.providers.arm.models import ArmResource, RoleAssignment
from mockazure.providers.arm.mutations import body, transaction
from mockazure.providers.arm.provider_registry import resolve_operation
from mockazure.providers.arm.provider_routes import provider_transaction
from mockazure.providers.arm.scopes import parse_scope
from mockazure.storage.accounts import TYPE, account_name, keys_view, mutate_account, record
from mockazure.storage.endpoints import account_endpoints
from mockazure.storage.models import StorageAccount

router = APIRouter()
BASE = (
    "/subscriptions/{subscription}/resourcegroups/{group}"
    "/providers/microsoft.storage/storageaccounts"
)
SUB = "/subscriptions/{subscription}/providers/microsoft.storage"


def parameters(request):
    resolve_operation(request.method, request.url.path, request.query_params.get("api-version"))
    if set(request.query_params) != {"api-version"} or len(request.query_params.multi_items()) != 1:
        raise ArmError(400, "InvalidQueryParameterValue", "Unsupported Storage provider query")


def view(row, request):
    result = copy.deepcopy(inventory_view(row) if isinstance(row, ArmResource) else row)
    profile = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
    result["properties"]["primaryEndpoints"] = account_endpoints(
        result["name"], result["kind"], profile.model_dump()
    )
    validate_arm_payload("StorageAccount", result, namespace="storage")
    return result


async def accounts_list(request, subscription, group=None):
    parameters(request)
    scope = request_scope(subscription, group)
    async with provider_transaction(request, scope, mutating=False) as (session, actor, sub):
        grants = await grant_query(session, actor, TYPE + "/read")
        if not await session.scalar(
            grants.where(RoleAssignment.subscription_id == scope.subscription_id).limit(1)
        ):
            raise ArmError(403, "AuthorizationFailed", "No accessible Storage accounts")
        # Scope-filtered SQL prevents a subscription list from exposing unrelated accounts.
        visible = await readable_scopes(session, actor, TYPE + "/read")
        query = (
            select(ArmResource)
            .join(
                StorageAccount,
                (StorageAccount.tenant_id == ArmResource.tenant_id)
                & (StorageAccount.scope_key == ArmResource.scope_key),
            )
            .where(
                ArmResource.tenant_id == actor.tenant_id,
                ArmResource.subscription_id == scope.subscription_id,
                ArmResource.resource_type == TYPE,
                visible,
            )
            .order_by(ArmResource.scope_key)
        )
        if group:
            await require_action(session, actor, scope, TYPE + "/read")
            query = query.where(ArmResource.group_key == scope.key)
        rows = await session.scalars(query.limit(1001))
        return {"value": [view(row, request) for row in rows]}


@router.get(SUB + "/storageaccounts")
async def list_subscription(subscription: str, request: Request):
    return await accounts_list(request, subscription)


@router.get(BASE)
async def list_group(subscription: str, group: str, request: Request):
    return await accounts_list(request, subscription, group)


@router.post(SUB + "/checknameavailability")
async def check_name(subscription: str, request: Request):
    parameters(request)
    value = await body(request)
    scope = request_scope(subscription)
    async with transaction(request, scope) as (session, actor, sub):
        await require_action(session, actor, scope, "Microsoft.Storage/checkNameAvailability/read")
        validate_arm_payload(
            "StorageAccountCheckNameAvailabilityParameters", value, namespace="storage"
        )
        if set(value) != {"name", "type"} or value["type"] != TYPE:
            raise ValueError("Invalid name availability request")
        try:
            account_name(value["name"])
        except ValueError:
            return {
                "nameAvailable": False,
                "reason": "AccountNameInvalid",
                "message": "Invalid Storage account name",
            }
        exists = await session.scalar(
            select(StorageAccount.name).where(StorageAccount.name == value["name"])
        )
        return (
            {"nameAvailable": False, "reason": "AlreadyExists", "message": "Name is unavailable"}
            if exists
            else {"nameAvailable": True}
        )


def account_scope(subscription, group, account):
    parent = request_scope(subscription, group)
    account_name(account)
    return parse_scope(parent.display_id + "/providers/" + TYPE + "/" + account)


@router.api_route(BASE + "/{account}", methods=["GET", "PUT", "PATCH", "DELETE"])
async def account_route(subscription: str, group: str, account: str, request: Request):
    parameters(request)
    try:
        scope = account_scope(subscription, group, account)
    except ValueError:
        raise ArmError(400, "AccountNameInvalid", "Invalid Storage account name") from None
    if request.method == "GET":
        async with provider_transaction(request, request_scope(subscription), mutating=False) as (
            session,
            actor,
            sub,
        ):
            await require_action(session, actor, scope, TYPE + "/read")
            if await session.get(StorageAccount, (actor.tenant_id, scope.key)) is None:
                raise ArmError(
                    409,
                    "InvalidOperation",
                    "Inventory-only account has no operational configuration",
                )
            return view(await resolve_scope(session, actor.tenant_id, scope), request)
    value = await body(request) if request.method in {"PUT", "PATCH"} else {}
    async with transaction(request, scope) as (session, actor, sub):
        session.info["storage_cipher"] = read_wrapping_key(request.app.state.settings.data_dir)
        result = await mutate_account(
            session,
            actor,
            scope.display_id,
            {"PUT": "create", "PATCH": "update", "DELETE": "delete"}[request.method],
            value,
            datetime.now(UTC),
        )
        return view(result.body, request) if result.body else Response(status_code=result.status)


@router.post(BASE + "/{account}/{action}")
async def keys_route(subscription: str, group: str, account: str, action: str, request: Request):
    parameters(request)
    value = await body(request) if action.lower() == "regeneratekey" else {}
    try:
        scope = account_scope(subscription, group, account)
    except ValueError:
        raise ArmError(400, "AccountNameInvalid", "Invalid Storage account name") from None
    async with transaction(request, scope) as (session, actor, sub):
        session.info["storage_cipher"] = read_wrapping_key(request.app.state.settings.data_dir)
        if action.lower() == "regeneratekey":
            return (
                await mutate_account(
                    session, actor, scope.display_id, "rotate", value, datetime.now(UTC)
                )
            ).body
        await require_action(session, actor, scope, TYPE + "/listKeys/action")
        if (await request.body()).strip() not in {b"", b"{}"}:
            raise ValueError("listKeys does not accept properties")
        row = await session.get(StorageAccount, (actor.tenant_id, scope.key))
        if not row:
            raise ArmError(409, "InvalidOperation", "Inventory-only account has no credentials")
        return keys_view(session, row)


@router.api_route(BASE + "/{account}/{service}/default", methods=["GET", "PUT"])
async def service_route(
    subscription: str, group: str, account: str, service: str, request: Request
):
    parameters(request)
    service = service.lower().removesuffix("services")
    if service not in {"blob", "file", "queue", "table"}:
        raise ArmError(501, "MockAzureOperationNotImplemented", "Unknown service")
    try:
        scope = account_scope(subscription, group, account)
    except ValueError:
        raise ArmError(400, "AccountNameInvalid", "Invalid Storage account name") from None
    supplied = await body(request) if request.method == "PUT" else None
    async with transaction(request, scope) as (session, actor, sub):
        action = TYPE + "/" + service + "Services/" + ("write" if supplied is not None else "read")
        await require_action(session, actor, scope, action)
        row = await resolve_scope(session, actor.tenant_id, scope)
        group_row = await resolve_scope(
            session, actor.tenant_id, request_scope(subscription, group)
        )
        if supplied is not None and group_row.provisioning_state == "Deleting":
            raise ArmError(
                409, "ResourceGroupBeingDeleted", "Resource group deletion is in progress"
            )
        if service not in account_endpoints(
            account, row.payload["kind"], request.app.state.settings.endpoints.model_dump()
        ):
            raise ArmError(400, "InvalidOperation", "Account kind does not support this service")
        state = await session.get(StorageAccount, (actor.tenant_id, scope.key))
        if state is None:
            raise ArmError(
                409, "InvalidOperation", "Inventory-only account has no operational services"
            )
        before = copy.deepcopy(state.services.get(service, {"cors": {"corsRules": []}}))
        if supplied is not None:
            validate_arm_payload(
                service.title() + "ServiceProperties", supplied, namespace="storage"
            )
            # Retention/versioning joins this subset with the data plane.
            if set(supplied) != {"properties"} or set(supplied["properties"]) - {"cors"}:
                raise ValueError("Only CORS service properties are supported in this phase")
            after = before | supplied["properties"]
            rules = after.get("cors", {}).get("corsRules", [])
            if len(rules) > 5:
                raise ValueError("At most five CORS rules are supported")
            if before != after:
                state.services = {**state.services, service: after}
                await record(session, actor, scope, action, before, after, datetime.now(UTC))
        else:
            after = before
        result = {
            "id": scope.display_id + "/" + service + "Services/default",
            "name": "default",
            "type": TYPE + "/" + service + "Services",
            "properties": after,
        }
        validate_arm_payload(service.title() + "ServiceProperties", result, namespace="storage")
        return result
