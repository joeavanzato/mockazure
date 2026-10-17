"""Separate host-local App Service token endpoint; no public provider fallback."""

import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from ipaddress import ip_address
from uuid import uuid4

from fastapi import FastAPI, Request
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from mockazure.db.engine import close_database, create_database
from mockazure.managed_identity.schemas import ManagedTokenRequest
from mockazure.managed_identity.service import ManagedIdentityError, acquire_managed_token


def error_response(error):
    return JSONResponse(
        status_code=error.status,
        content={
            "statusCode": error.status,
            "message": error.message,
            "error": error.error,
            "error_description": error.message,
        },
        headers={"Cache-Control": "no-store"},
    )


def unavailable():
    return ManagedIdentityError(
        503, "temporarily_unavailable", "Local managed identity is unavailable"
    )


def create_managed_identity_app(settings, database=None):
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

    @app.exception_handler(ManagedIdentityError)
    async def managed_error(request, error):
        if error.authentication_fact is not None:
            from mockazure.evidence.authentication import record_authentication_failure

            try:
                await record_authentication_failure(app.state.database, error.authentication_fact)
            except (SQLAlchemyError, ValueError, TimeoutError):
                return error_response(unavailable())
        return error_response(error)

    @app.exception_handler(SQLAlchemyError)
    async def storage_error(request, error):
        return error_response(unavailable())

    @app.get("/MSI/token")
    async def token(request: Request):
        authority = request.headers.getlist("host")
        match = re.fullmatch(
            r"([a-zA-Z0-9][a-zA-Z0-9.-]*)(?::([0-9]{1,5}))?",
            authority[0] if len(authority) == 1 else "",
        )
        if (
            not match
            or match[1].lower() not in settings.allowed_hosts
            or (match[2] is not None and not 1 <= int(match[2]) <= 65535)
        ):
            raise ManagedIdentityError(400, "invalid_request", "Host is not configured")
        headers = request.headers.getlist("x-identity-header")
        if len(headers) != 1 or not 1 <= len(headers[0]) <= 512:
            raise ManagedIdentityError(403, "access_denied", "A host credential is required")
        pairs = list(request.query_params.multi_items())
        values = dict(pairs)
        selectors = set(values) & {"client_id", "principal_id", "object_id", "mi_res_id"}
        if (
            len(pairs) != len(values)
            or set(values) - {"api-version", "resource"} - selectors
            or len(selectors) > 1
        ):
            raise ManagedIdentityError(
                400, "invalid_request", "Invalid managed identity parameters"
            )
        selector = next(iter(selectors), None)
        peer = None
        try:
            if request.client:
                peer = str(ip_address(request.client.host))
        except ValueError:
            pass
        try:
            value = ManagedTokenRequest(
                header=headers[0],
                api_version=values.get("api-version", ""),
                resource=values.get("resource", ""),
                selector=selector,
                selector_value=values.get(selector) if selector else None,
                attempt_id=uuid4(),
                ip_address=peer,
            )
        except ValidationError:
            raise ManagedIdentityError(
                400, "invalid_request", "Invalid managed identity parameters"
            ) from None
        async with app.state.database.sessions.begin() as session:
            result = await acquire_managed_token(session, value, settings, datetime.now(UTC))
        return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "no-store"})

    return app
