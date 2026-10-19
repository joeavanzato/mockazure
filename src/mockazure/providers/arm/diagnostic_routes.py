"""Exact diagnostic extension-resource paths, separate from generic ARM inventory."""

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Request, Response
from starlette.responses import JSONResponse

from mockazure.cloud.lifecycle import lock_lifecycle
from mockazure.monitor.diagnostics import categories, mutate_diagnostic_setting
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.probes import principal
from mockazure.providers.arm.provider_registry import resolve_operation

router = APIRouter()


async def handle(request, resource_path, name=None, *, category=False):
    version = request.query_params.get("api-version")
    versions = {"2017-05-01-preview", "2021-05-01-preview"} if category else {"2021-05-01-preview"}
    if version not in versions or list(request.query_params.multi_items()) != [
        ("api-version", version)
    ]:
        raise ArmError(400, "InvalidApiVersionParameter", "Unsupported diagnostic API version")
    resolve_operation(request.method, request.url.path, version)
    body = None
    if request.method == "PUT":
        raw = await request.body()
        if len(raw) > 65536:
            raise ArmError(413, "RequestTooLarge", "Diagnostic settings exceed local limit")
        try:
            body = json.loads(raw)
        except ValueError:
            raise ArmError(400, "BadRequest", "JSON diagnostic properties required") from None
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        session.info["local_write_headers"] = request.headers
        await lock_lifecycle(session, actor.tenant_id)
        if category:
            result = await categories(session, actor, "/" + resource_path, name)
            if version == "2017-05-01-preview":
                for item in [result] if name else result["value"]:
                    item["properties"].pop("categoryGroups", None)
            schema = "DiagnosticSettingsCategoryResource" + ("" if name else "Collection")
            validate_arm_payload(
                schema,
                result,
                namespace="diagnosticCategories2021"
                if version == "2021-05-01-preview"
                else "diagnosticCategories",
            )
            return result
        operation = "list" if name is None and request.method == "GET" else request.method.lower()
        try:
            result = await mutate_diagnostic_setting(
                session, actor, "/" + resource_path, name, operation, body, datetime.now(UTC)
            )
        except ValueError:
            raise ArmError(
                400, "InvalidResourceId", "Invalid diagnostic source or destination"
            ) from None
    return (
        Response(status_code=result.status)
        if result.body is None
        else JSONResponse(result.body, status_code=result.status, headers=result.headers)
    )


@router.get("/{resource_path:path}/providers/microsoft.insights/diagnosticsettingscategories")
@router.get(
    "/{resource_path:path}/providers/microsoft.insights/diagnosticsettingscategories/{name}"
)
async def category_route(request: Request, resource_path: str, name: str | None = None):
    return await handle(request, resource_path, name, category=True)


@router.get("/{resource_path:path}/providers/microsoft.insights/diagnosticsettings")
@router.api_route(
    "/{resource_path:path}/providers/microsoft.insights/diagnosticsettings/{name}",
    methods=["GET", "PUT", "DELETE"],
)
async def setting_route(request: Request, resource_path: str, name: str | None = None):
    return await handle(request, resource_path, name)
