from datetime import datetime, timedelta
from uuid import UUID

import jwt
from sqlalchemy import select

from mockazure.db.models import Tenant
from mockazure.endpoints import RESOURCES, EndpointProfile
from mockazure.identity.key_models import SigningKey
from mockazure.identity.keys import CLOCK_SKEW, TOKEN_SECONDS, SigningKeyStore
from mockazure.identity.types import PrincipalContext
from mockazure.oauth.errors import OAuthError


async def issue_access_token(
    context: PrincipalContext,
    now: datetime,
    keys: SigningKeyStore,
    *,
    managed_resource_id: str | None = None,
) -> str:
    key = await keys.active_key(now)
    claims = {
        "iss": keys.endpoints.issuer(str(context.tenant_id)),
        "aud": context.audience,
        "tid": str(context.tenant_id),
        "azp": str(context.client_id),
        "appid": str(context.client_id),
        "oid": str(context.subject_id or context.client_id),
        "sub": str(context.subject_id or context.client_id),
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(now.timestamp()) + TOKEN_SECONDS,
        "ver": "2.0",
    }
    if managed_resource_id is not None:
        if context.token_kind != "application" or not managed_resource_id.startswith("/"):
            raise ValueError("Invalid managed identity context")
        claims["xms_mirid"] = managed_resource_id
    if context.token_kind == "application":
        claims.update(idtyp="app", roles=sorted(context.permissions))
    else:
        claims["scp"] = " ".join(sorted(context.permissions))
        if context.session_id:
            claims["sid"] = str(context.session_id)
    return jwt.encode(
        claims, keys.private_key(key), algorithm="RS256", headers={"kid": key.kid, "typ": "at+jwt"}
    )


async def validate_access_token(
    raw: str, service: str, now: datetime, session, *, endpoints: EndpointProfile
) -> PrincipalContext:
    try:
        if len(raw) > 32768:
            raise ValueError()
        header = jwt.get_unverified_header(raw)
        if header.get("alg") != "RS256" or header.get("typ") != "at+jwt":
            raise ValueError()
        kid = header.get("kid")
        if not isinstance(kid, str) or len(kid) != 32:
            raise ValueError()
        key = await session.get(SigningKey, kid)
        if key is None or (
            key.retired_at and key.retired_at + timedelta(seconds=TOKEN_SECONDS + CLOCK_SKEW) <= now
        ):
            raise ValueError()
        unsigned = jwt.decode(raw, options={"verify_signature": False})
        if not isinstance(unsigned.get("tid"), str):
            raise ValueError()
        tenant = UUID(unsigned["tid"])
        resource = next(r for r in RESOURCES if r.service == service)
        claims = jwt.decode(
            raw,
            jwt.PyJWK.from_dict(key.public_jwk).key,
            algorithms=["RS256"],
            audience=resource.audience,
            issuer=endpoints.issuer(str(tenant)),
            options={
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
                "require": ["exp", "nbf", "iat", "tid", "iss", "aud", "azp", "oid"],
            },
        )
        if claims["aud"] != resource.audience:
            raise ValueError()
        if any(type(claims[name]) is not int for name in ("exp", "nbf", "iat")):
            raise ValueError()
        timestamp = now.timestamp()
        if claims["exp"] <= timestamp - CLOCK_SKEW or claims["nbf"] > timestamp + CLOCK_SKEW:
            raise ValueError()
        if claims["iat"] > timestamp + CLOCK_SKEW or claims["exp"] <= claims["iat"]:
            raise ValueError()
        if not await session.scalar(select(Tenant.id).where(Tenant.id == tenant)):
            raise ValueError()
        kind = "application" if claims.get("idtyp") == "app" else "delegated"
        if kind == "application":
            permissions = claims.get("roles", [])
            if "scp" in claims or not isinstance(permissions, list):
                raise ValueError()
        else:
            if not isinstance(claims.get("scp"), str) or "roles" in claims:
                raise ValueError()
            permissions = claims["scp"].split()
        if len(permissions) > 64 or any(
            not isinstance(p, str) or len(p) > 200 for p in permissions
        ):
            raise ValueError()
        for name in ("azp", "oid", "sid"):
            if name in claims and not isinstance(claims[name], str):
                raise ValueError()
        return PrincipalContext(
            tenant,
            UUID(claims["azp"]),
            UUID(claims["oid"]),
            kind,
            resource.audience,
            frozenset(permissions),
            UUID(claims["sid"]) if claims.get("sid") else None,
        )
    except (jwt.PyJWTError, ValueError, KeyError, TypeError, StopIteration):
        raise OAuthError(
            "invalid_token", "Access token is invalid for this resource", 401
        ) from None
