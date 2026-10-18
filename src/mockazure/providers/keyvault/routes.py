"""Dedicated Key Vault listener and service-owned authentication challenge."""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from mockazure.cloud.lifecycle import lock_lifecycle
from mockazure.identity.keys import read_wrapping_key
from mockazure.identity.tokens import validate_access_token
from mockazure.keyvault.contracts import bad, validate_shape
from mockazure.keyvault.endpoints import selected_profile, virtual_vault
from mockazure.keyvault.models import Vault
from mockazure.keyvault.secrets import get_secret, list_secrets, mutate_secret
from mockazure.oauth.errors import OAuthError
from mockazure.providers.keyvault.errors import VaultError
from mockazure.providers.storage.errors import StorageError
from mockazure.providers.storage.routes import bounded_body

VERSION = "2025-07-01"


def challenge(settings, profile, tenant):
    return {
        "WWW-Authenticate": 'Bearer authorization="'
        + profile.login.rstrip("/")
        + "/"
        + str(tenant)
        + '/oauth2/authorize", resource="https://vault.azure.net", scope="https://vault.azure.net/.default"'
    }


def body_json(raw):
    def pairs(values):
        result = {}
        for k, v in values:
            if k in result:
                bad("Duplicate JSON field")
            result[k] = v
        return result

    try:
        return json.loads(
            raw, object_pairs_hook=pairs, parse_constant=lambda _: bad("Nonfinite JSON value")
        )
    except (ValueError, UnicodeError, RecursionError):
        bad("Malformed JSON request")


async def handle(request):
    headers = {"x-ms-request-id": request.state.request_id, "Cache-Control": "no-store"}
    try:
        body = await dispatch(request)
        return JSONResponse(body, headers=headers)
    except VaultError as error:
        return JSONResponse(error.body(), status_code=error.status, headers=headers | error.headers)
    except StorageError as error:
        return JSONResponse(
            {"error": {"code": "BadParameter", "message": "Request body exceeds local limit"}},
            status_code=error.status,
            headers=headers,
        )
    except SQLAlchemyError:
        return JSONResponse(
            {
                "error": {
                    "code": "ServiceUnavailable",
                    "message": "Key Vault database is unavailable",
                }
            },
            status_code=503,
            headers=headers,
        )


async def dispatch(request):
    settings = request.app.state.settings
    authority = request.headers.get("host", "")
    profile = selected_profile(settings, authority)
    if request.url.scheme != "https" or profile is None:
        bad("Host or scheme does not match Key Vault listener")
    virtual_name = virtual_vault(authority, profile.keyvault)
    args = request.query_params
    if len(args) != len(args.multi_items()) or args.get("api-version") != VERSION:
        bad("Invalid query or unsupported API version")
    parts = request.scope["path"].strip("/").split("/")
    if virtual_name:
        parts.insert(0, virtual_name)
    if len(parts) < 2 or parts[1] != "secrets" or len(parts) > 4:
        raise VaultError(501, "NotImplemented", "This Key Vault operation is not implemented")
    vault_name = parts[0].lower()
    name = parts[2] if len(parts) >= 3 else None
    version = parts[3] if len(parts) == 4 else None
    listing = request.method == "GET" and (name is None or version == "versions")
    allowed = {"api-version", "maxresults", "$skiptoken"} if listing else {"api-version"}
    if set(args) - allowed:
        bad("Unsupported Key Vault query option")
    if any(k.startswith("if-") for k in request.headers):
        bad("Conditional headers are unsupported")
    raw = await bounded_body(request, 196608)
    async with request.app.state.database.sessions() as session:
        vault = await session.scalar(select(Vault).where(Vault.name == vault_name))
        if vault is None:
            raise VaultError(404, "VaultNotFound", "The vault was not found")
        await lock_lifecycle(session, vault.tenant_id)
        now = datetime.now(UTC)
        auth = request.headers.get("authorization", "")
        challenges = challenge(settings, profile, vault.tenant_id)
        if not auth.startswith("Bearer "):
            raise VaultError(401, "Unauthorized", "Bearer authentication is required", challenges)
        try:
            actor = await validate_access_token(
                auth[7:], "keyvault", now, session, endpoints=settings.endpoints
            )
        except OAuthError:
            raise VaultError(
                401, "Unauthorized", "The access token is invalid", challenges
            ) from None
        if actor.tenant_id != vault.tenant_id:
            raise VaultError(403, "Forbidden", "The token belongs to another tenant")
        body = {}
        if request.method in {"PUT", "PATCH"}:
            if not request.headers.get("content-type", "").lower().startswith("application/json"):
                bad("JSON Content-Type required")
            body = body_json(raw)
        elif raw:
            bad("This operation requires an empty body")
        session.info.update(
            keyvault_cipher=read_wrapping_key(settings.data_dir),
            keyvault_endpoint=profile.keyvault,
            keyvault_request_base=(str(request.url).split("?", 1)[0].split("/secrets", 1)[0]),
            keyvault_peer=request.client.host if request.client else None,
        )
        if listing:
            result = await list_secrets(session, actor, vault.scope_key, name, args, now)
            validate_shape("SecretListResult", result)
        elif name and request.method == "GET":
            result = await get_secret(session, actor, vault.scope_key, name, version, now)
            validate_shape("SecretBundle", result)
        elif (
            name
            and request.method in {"PUT", "PATCH"}
            and (request.method != "PUT" or version is None)
        ):
            result = await mutate_secret(
                session,
                actor,
                vault.scope_key,
                name,
                version,
                "set" if request.method == "PUT" else "update",
                body,
                now,
            )
            validate_shape("SecretBundle", result)
        else:
            raise VaultError(501, "NotImplemented", "This secret operation is not implemented")
        await session.commit()
        return result
