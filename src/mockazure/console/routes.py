"""Exact console endpoints and packaged static assets."""

from datetime import UTC, datetime
from importlib.resources import files
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import SecretStr

from mockazure.admin.errors import AdminError
from mockazure.admin.profile_routes import ProfileRoute
from mockazure.console.boundary import require_origin
from mockazure.console.inspector import descriptors, inspect_table
from mockazure.console.sessions import COOKIE, authenticate, login
from mockazure.population.types import AdapterContext, Input
from mockazure.providers.graph.body import read_json

ASSETS = {
    "index.html": "text/html",
    "styles.css": "text/css",
    **{
        name + ".js": "text/javascript"
        for name in ("app", "api", "resources", "profiles", "runs", "inspector")
    },
}
PUBLIC_PATHS = {"/_mock/console/login", "/_mock/ui/", *{"/_mock/ui/" + name for name in ASSETS}}
router = APIRouter(route_class=ProfileRoute)
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' data:; font-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)


def enabled(request):
    if not request.app.state.settings.console_enabled:
        raise AdminError(404, "ConsoleDisabled", "Console is disabled")


class LoginInput(Input):
    password: SecretStr


@router.post("/_mock/console/login")
async def login_route(request: Request):
    enabled(request)
    require_origin(request)
    data = LoginInput.model_validate(await read_json(request, limit=4096))
    error, issued = None, None
    async with request.app.state.database.sessions.begin() as session:
        try:
            issued = await login(
                session,
                data.password.get_secret_value(),
                request.client.host if request.client else "unknown",
                datetime.now(UTC),
            )
        except AdminError as exc:
            # Commit failed-attempt accounting before returning the authentication error.
            error = exc
    if error:
        return JSONResponse(
            {"error": {"code": error.code, "message": error.message}},
            status_code=error.status,
            headers={"Retry-After": "60", "Cache-Control": "no-store"}
            if error.status == 429
            else {"Cache-Control": "no-store"},
        )
    response = JSONResponse(
        {"csrf_token": issued.csrf_token, "expires_at": issued.expires_at.isoformat()},
        headers={"Cache-Control": "no-store"},
    )
    response.set_cookie(
        COOKIE,
        issued.cookie,
        max_age=8 * 3600,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/_mock",
    )
    return response


@router.get("/_mock/console/session")
async def current_session(request: Request):
    enabled(request)
    async with request.app.state.database.sessions() as session:
        row = await authenticate(session, request.cookies.get(COOKIE), datetime.now(UTC))
        if row is None:
            raise AdminError(401, "SessionRequired", "Log in with the shared lab password")
        return JSONResponse(
            {
                "csrf_token": row.csrf_token,
                "expires_at": row.expires_at.isoformat(),
                "inspector_enabled": request.app.state.settings.console_inspector_enabled,
            },
            headers={"Cache-Control": "no-store"},
        )


@router.post("/_mock/console/logout")
async def logout(request: Request):
    enabled(request)
    async with request.app.state.database.sessions.begin() as session:
        row = await authenticate(session, request.cookies.get(COOKIE), datetime.now(UTC))
        if row:
            row.revoked = True
    response = JSONResponse({"logged_out": True}, headers={"Cache-Control": "no-store"})
    response.delete_cookie(COOKIE, path="/_mock", secure=True, httponly=True, samesite="strict")
    return response


@router.get("/_mock/console/inspector")
async def inspector(
    request: Request,
    table_key: str | None = Query(None, max_length=128),
    tenant_id: UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    continuation: str | None = Query(None, max_length=8192),
):
    enabled(request)
    if not request.app.state.settings.console_inspector_enabled:
        raise AdminError(404, "InspectorDisabled", "Inspector is disabled")
    if table_key is None:
        return {"tables": descriptors()}
    ctx = AdapterContext(request.app.state.database, request.app.state.settings, tenant_id)
    return await inspect_table(ctx, table_key, tenant_id, limit, continuation)


@router.get("/_mock/ui/")
async def shell(request: Request):
    return await static("index.html", request)


@router.get("/_mock/ui/{asset}")
async def static(asset: str, request: Request):
    enabled(request)
    if asset not in ASSETS:
        raise AdminError(404, "AssetNotFound", "Console asset does not exist")
    return Response(
        files("mockazure.console").joinpath("static", asset).read_bytes(),
        media_type=ASSETS[asset],
        headers={
            "Content-Security-Policy": CSP,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )
