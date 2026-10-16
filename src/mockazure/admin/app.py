from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException

from mockazure.admin.auth import verify_admin_token
from mockazure.admin.errors import AdminError, error_response
from mockazure.admin.routes import router
from mockazure.config import Settings
from mockazure.db.engine import Database, close_database, create_database
from mockazure.tenants.repository import TenantConflict


def create_app(settings: Settings, database: Database | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        app.state.database = database or create_database(settings)
        try:
            yield
        finally:
            if database is None:
                await close_database(app.state.database)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    if database is not None:
        app.state.database = database

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        request.state.request_id = str(uuid4())
        try:
            host = request.headers.get("host", "").split(":")[0].lower()
            if host not in settings.allowed_hosts:
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
            response = await call_next(request)
        except Exception:
            # Do not propagate raw exception messages into ASGI logs or responses.
            response = error_response(
                request, 500, "InternalError", "Request could not be completed"
            )
        response.headers["x-request-id"] = request.state.request_id
        return response

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

    app.include_router(router)
    return app
