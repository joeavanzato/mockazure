"""Published VM actions and opaque local ARM asynchronous-operation monitor URLs."""

from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from mockazure.db.job_model import Job
from mockazure.providers.arm.authorization import grant_query, require_action
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.inventory import resolve_scope
from mockazure.providers.arm.models import RoleAssignment, VmState
from mockazure.providers.arm.mutations import transaction
from mockazure.providers.arm.probes import principal
from mockazure.providers.arm.provider_jobs import KIND, POWER, start_operation
from mockazure.providers.arm.provider_registry import resolve_operation
from mockazure.providers.arm.scopes import parse_scope

router = APIRouter()


def links(request, job):
    profile = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
    scope = parse_scope(job.payload["scope"])
    base = (
        profile.arm
        + f"/subscriptions/{scope.subscription_id}/providers/{job.payload['namespace']}"
        + f"/locations/{job.payload['location']}"
    )
    query = "?" + urlencode({"api-version": job.payload["version"]})
    return {
        "Azure-AsyncOperation": base + "/operations/" + str(job.id) + query,
        "Location": base + "/operationResults/" + str(job.id) + query,
        "Retry-After": "1",
    }


def accepted(request, job):
    return Response(status_code=202, headers=links(request, job))


@router.api_route(
    "/subscriptions/{subscription}/resourcegroups/{group}/providers/Microsoft.Compute/virtualMachines/{name}/{action}",
    methods=["POST", "GET"],
)
async def vm_operation(subscription: str, group: str, name: str, action: str, request: Request):
    # This precise route precedes generic provider paths; defer unrelated extension collections.
    if action.lower() == "extensions" and request.method == "GET":
        from mockazure.providers.arm.resource_handlers import group_resource

        return await group_resource(
            subscription, group, "Microsoft.Compute", f"virtualMachines/{name}/extensions", request
        )
    operation = resolve_operation(
        request.method, request.url.path, request.query_params.get("api-version")
    )
    if operation["operationId"] not in {
        "VirtualMachines_" + v
        for v in ("Start", "Restart", "PowerOff", "Deallocate", "InstanceView")
    }:
        raise ArmError(501, "MockAzureOperationNotImplemented", "VM operation is not modeled")
    if len(request.query_params) != len(request.query_params.multi_items()) or set(
        request.query_params
    ) - set(operation["queryOptions"]):
        raise ArmError(400, "InvalidQueryParameterValue", "Unsupported VM option")
    for key in ("hibernate", "skipShutdown"):
        if key in request.query_params and request.query_params[key] not in {"true", "false"}:
            raise ArmError(400, "InvalidQueryParameterValue", "Boolean query value required")
    if request.query_params.get("hibernate") == "true":
        raise ArmError(400, "InvalidParameter", "Hibernation is outside this profile")
    if await request.body():
        raise ArmError(400, "InvalidRequestContent", "This operation has no request body")
    scope = parse_scope(
        f"/subscriptions/{subscription}/resourceGroups/{group}/providers/Microsoft.Compute/virtualMachines/{name}"
    )
    if operation["operationId"] == "VirtualMachines_InstanceView":
        async with request.app.state.database.sessions() as session:
            actor = await principal(request, session)
            await require_action(
                session, actor, scope, "Microsoft.Compute/virtualMachines/instanceView/read"
            )
            row = await resolve_scope(session, actor.tenant_id, scope)
            vm = await session.get(VmState, (actor.tenant_id, scope.key))
            if vm is None:
                raise ArmError(409, "InvalidOperation", "VM is inventory-only")
            result = {
                "statuses": [
                    {
                        "code": "ProvisioningState/" + row.provisioning_state.lower(),
                        "level": "Info",
                        "displayStatus": "Provisioning " + row.provisioning_state.lower(),
                    },
                    {
                        "code": "PowerState/" + vm.power_state,
                        "level": "Info",
                        "displayStatus": "VM " + vm.power_state,
                    },
                ]
            }
            validate_arm_payload("VirtualMachineInstanceView", result, namespace="compute")
            return result
    action = next(v for v in POWER if v.lower() == action.lower())
    async with transaction(request, scope) as (session, actor, sub):
        job = await start_operation(session, actor, scope, action, operation["version"])
        return accepted(request, job)


@router.get(
    "/subscriptions/{subscription}/providers/{namespace}/locations/{location}/{monitor}/{identifier}"
)
async def poll(
    subscription: str,
    namespace: str,
    location: str,
    monitor: str,
    identifier: str,
    request: Request,
):
    if monitor.lower() not in {"operations", "operationresults"}:
        raise ArmError(501, "MockAzureOperationNotImplemented", "Unsupported operation monitor")
    try:
        identifier = UUID(identifier)
        subscription = UUID(subscription)
    except ValueError:
        raise ArmError(400, "InvalidOperationId", "Invalid operation identity") from None
    async with request.app.state.database.sessions() as session:
        actor = await principal(request, session)
        job = await session.get(Job, identifier)
        if job is None or job.tenant_id != actor.tenant_id or job.kind != KIND:
            raise ArmError(404, "OperationNotFound", "Operation was not found")
        scope = parse_scope(job.payload["scope"])
        if (
            scope.subscription_id != subscription
            or job.payload["namespace"].lower() != namespace.lower()
            or job.payload["location"].lower() != location.lower()
        ):
            raise ArmError(404, "OperationNotFound", "Operation was not found")
        if (
            list(request.query_params.keys()) != ["api-version"]
            or len(request.query_params.multi_items()) != 1
            or request.query_params["api-version"] != job.payload["version"]
        ):
            raise ArmError(
                400, "InvalidApiVersionParameter", "Unsupported operation monitor version"
            )
        # Polling after deletion requires current authority at least at resource-group scope.
        grants = await grant_query(session, actor, job.payload["action"])
        rg = parse_scope(scope.parent_keys[1])
        if not await session.scalar(
            grants.where(RoleAssignment.scope_key.in_((scope.parent_keys[0], rg.key))).limit(1)
        ):
            raise ArmError(
                403, "AuthorizationFailed", "Operation monitor authority is missing or revoked"
            )
        status = {
            "pending": "InProgress",
            "running": "InProgress",
            "succeeded": "Succeeded",
            "failed": "Failed",
        }[job.state]
        if monitor.lower() == "operations":
            result = {
                "status": status,
                "name": str(job.id),
                "startTime": job.created_at.isoformat(),
            }
            if status in {"Succeeded", "Failed"}:
                result["endTime"] = job.updated_at.isoformat()
            if status == "Failed":
                result["error"] = {
                    "code": job.error_code or "OperationFailed",
                    "message": "Provider operation failed",
                }
            return JSONResponse(
                result, headers={"Retry-After": "1"} if status == "InProgress" else {}
            )
        if status == "InProgress":
            return accepted(request, job)
        if status == "Failed":
            raise ArmError(409, job.error_code or "OperationFailed", "Provider operation failed")
        return Response(status_code=204 if job.payload["operation"] == "delete" else 200)
