import re
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from mockazure.admin.arm_routes import router as arm_admin_router
from mockazure.admin.auth import verify_admin_token
from mockazure.admin.directory_routes import router as directory_admin_router
from mockazure.admin.errors import AdminError
from mockazure.admin.errors import error_response as admin_error_response
from mockazure.admin.evidence_routes import router as evidence_admin_router
from mockazure.admin.graph_routes import router as graph_admin_router
from mockazure.admin.identity_routes import router as identity_router
from mockazure.admin.job_routes import router as job_router
from mockazure.admin.licensing_routes import router as licensing_admin_router
from mockazure.admin.mail_routes import router as mail_admin_router
from mockazure.admin.managed_identity_routes import router as managed_identity_admin_router
from mockazure.admin.routes import router
from mockazure.admin.security_routes import router as security_admin_router
from mockazure.admin.session_routes import router as session_router
from mockazure.config import Settings
from mockazure.db.engine import Database, close_database, create_database
from mockazure.identity.repository import IdentityError
from mockazure.oauth.authorization import router as authorization_router
from mockazure.oauth.device import router as device_router
from mockazure.oauth.discovery import router as discovery_router
from mockazure.oauth.errors import OAuthError, oauth_error_response
from mockazure.oauth.routes import router as oauth_router
from mockazure.providers.arm.probes import router as arm_router
from mockazure.providers.exchange.probes import router as exchange_router
from mockazure.providers.graph.directory import router_for
from mockazure.providers.graph.errors import GraphError, graph_error_response
from mockazure.providers.graph.evidence.routes import router_for as evidence_router_for
from mockazure.providers.graph.licensing import router as licensing_graph_router
from mockazure.providers.graph.mail_routes import router_for as mail_router_for
from mockazure.providers.graph.probes import router as graph_router
from mockazure.providers.graph.security.grant_routes import router_for as grant_router_for
from mockazure.providers.graph.security.method_routes import router_for as method_router_for
from mockazure.providers.graph.security.policy_routes import router_for as policy_router_for
from mockazure.providers.graph.security.report_routes import router_for as registration_router_for
from mockazure.providers.graph.security.role_routes import router_for as role_router_for
from mockazure.providers.graph.security.routes import router_for as security_router_for
from mockazure.providers.graph.throttle import enforce_directory_budget
from mockazure.tenants.repository import TenantConflict


def error_response(request, status, code, message):
    path = request.url.path
    if (
        path.startswith(("/v1.0/", "/beta/", "/adminapi/", "/AdminApi/"))
        or path == "/subscriptions"
    ):
        if status == 422:
            status, code = 400, "InvalidRequest"
        return graph_error_response(request, GraphError(status, code, message))
    if (
        "/oauth2/" in path
        or "/discovery/" in path
        or "/.well-known/" in path
        or path in ("/login", "/consent", "/mfa", "/device")
    ):
        oauth_code = (
            "temporarily_unavailable"
            if status == 503
            else ("server_error" if status >= 500 else "invalid_request")
        )
        return oauth_error_response(
            request, OAuthError(oauth_code, message, 400 if status == 422 else status)
        )
    return admin_error_response(request, status, code, message)


def create_app(settings: Settings, database: Database | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.database = database or create_database(settings)
        try:
            yield
        finally:
            if database is None:
                await close_database(app.state.database)

    app = FastAPI(
        lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False
    )
    app.state.settings = settings
    if database is not None:
        app.state.database = database

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        request.state.request_id = str(uuid4())
        try:
            authorities = request.headers.getlist("host")
            authority = authorities[0].lower() if len(authorities) == 1 else ""
            parsed = re.fullmatch(
                r"([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::([0-9]{1,5}))?", authority
            )
            if (
                parsed is None
                or parsed[1] not in settings.allowed_hosts
                or (parsed[2] is not None and not 1 <= int(parsed[2]) <= 65535)
            ):
                return error_response(request, 400, "InvalidHost", "Host is not configured")
            if request.url.path.startswith("/_mock") and request.url.path not in (
                "/_mock/health",
                "/_mock/ready",
            ):
                scheme, _, token = request.headers.get("authorization", "").partition(" ")
                if scheme.lower() != "bearer" or not verify_admin_token(token, settings.token_file):
                    response = error_response(
                        request, 401, "Unauthorized", "Management authentication required"
                    )
                    response.headers["WWW-Authenticate"] = "Bearer"
                    return response
            await enforce_directory_budget(request)
            response = await call_next(request)
        except GraphError as error:
            response = graph_error_response(request, error)
        except SQLAlchemyError:
            response = error_response(request, 503, "DatabaseUnavailable", "Database is not ready")
        except Exception:
            # Do not propagate raw exception messages into ASGI logs or responses.
            response = error_response(
                request, 500, "InternalError", "Request could not be completed"
            )
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(GraphError)
    async def graph_error(request, error):
        return graph_error_response(request, error)

    @app.exception_handler(OAuthError)
    async def oauth_error(request, error):
        fact = getattr(error, "authentication_fact", None)
        if fact is not None:
            from mockazure.evidence.authentication import record_authentication_failure

            try:
                await record_authentication_failure(request.app.state.database, fact)
            except (SQLAlchemyError, ValueError, TimeoutError):
                return oauth_error_response(
                    request,
                    OAuthError(
                        "temporarily_unavailable",
                        "Authentication evidence storage is unavailable",
                        503,
                    ),
                )
        return oauth_error_response(request, error)

    @app.exception_handler(IdentityError)
    async def identity_error(request, error):
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(AdminError)
    async def admin_error(request, error):
        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(TenantConflict)
    async def conflict(request, error):
        return error_response(
            request, 409, "TenantConflict", "Tenant slug or domain already exists"
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, error):
        return error_response(
            request, 422, "ValidationError", "Request does not match the supported schema"
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, error):
        return error_response(
            request, 503, "DatabaseUnavailable", "Database or schema is not ready"
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        if error.status_code == 404 and not request.url.path.startswith("/_mock"):
            return error_response(
                request,
                501,
                "MockAzureOperationNotImplemented",
                "This provider operation is not implemented",
            )
        return error_response(
            request,
            error.status_code,
            "MethodNotAllowed" if error.status_code == 405 else "NotFound",
            "Method not allowed" if error.status_code == 405 else "Resource not found",
        )

    app.include_router(mail_admin_router)
    app.include_router(router)
    app.include_router(evidence_admin_router)
    app.include_router(job_router)
    app.include_router(licensing_admin_router)
    app.include_router(managed_identity_admin_router)
    app.include_router(licensing_graph_router)
    app.include_router(arm_admin_router)
    app.include_router(arm_router)
    app.include_router(session_router)
    app.include_router(identity_router)
    app.include_router(security_admin_router)
    app.include_router(graph_admin_router)
    app.include_router(directory_admin_router)
    app.include_router(discovery_router)
    app.include_router(oauth_router)
    app.include_router(authorization_router)
    app.include_router(device_router)
    app.include_router(graph_router, prefix="/v1.0")
    app.include_router(graph_router, prefix="/beta")
    app.include_router(evidence_router_for("v1.0"))
    app.include_router(evidence_router_for("beta"))
    app.include_router(registration_router_for("v1.0"))
    app.include_router(registration_router_for("beta"))
    app.include_router(method_router_for("v1.0"))
    app.include_router(method_router_for("beta"))
    app.include_router(policy_router_for("v1.0"))
    app.include_router(policy_router_for("beta"))
    app.include_router(role_router_for("v1.0"))
    app.include_router(role_router_for("beta"))
    app.include_router(grant_router_for("v1.0"))
    app.include_router(grant_router_for("beta"))
    app.include_router(security_router_for("v1.0"))
    app.include_router(security_router_for("beta"))
    app.include_router(mail_router_for("v1.0"))
    app.include_router(mail_router_for("beta"))
    app.include_router(router_for("v1.0"))
    app.include_router(router_for("beta"))
    app.include_router(exchange_router)
    return app
