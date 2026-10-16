from sqlalchemy import or_, select

from mockazure.authorization.catalog import permission
from mockazure.endpoints import RESOURCES, resolve_resource
from mockazure.identity.models import Grant
from mockazure.oauth.errors import OAuthError

OIDC = frozenset({"openid", "profile", "email", "offline_access"})


def parse_scopes(raw, app, endpoints):
    requested = set(raw.split())
    if not requested or len(requested) > 64 or len(raw) > 8192:
        raise OAuthError("invalid_scope", "Specify supported scopes for one resource")
    resource = None
    names = set()
    for item in requested - OIDC:
        try:
            if "/" in item:
                alias, name = item.rsplit("/", 1)
                resolved = resolve_resource(alias, endpoints)
            else:
                resolved, name = RESOURCES[0], item
            if resource and resource != resolved.service:
                raise ValueError()
            resource = resolved.service
            if name == ".default":
                names.update(
                    p
                    for row in app.required_permissions
                    if row["resource"] == resource and row["kind"] == "delegated"
                    for p in row["permissions"]
                )
            else:
                permission(resource, "delegated", name)
                names.add(name)
        except ValueError:
            raise OAuthError("invalid_scope", "Unsupported or mixed-resource scopes") from None
    resource = resource or "graph"
    configured = {
        p
        for row in app.required_permissions
        if row["resource"] == resource and row["kind"] == "delegated"
        for p in row["permissions"]
    }
    if not names <= configured:
        raise OAuthError("invalid_scope", "Scopes are not configured for this client")
    return resource, sorted(names | (requested & OIDC))


async def granted_scopes(session, tenant_id, client_id, user_id, resource):
    grants = await session.scalars(
        select(Grant).where(
            Grant.tenant_id == tenant_id,
            Grant.client_id == client_id,
            Grant.resource == resource,
            Grant.kind == "delegated",
            or_(Grant.user_id.is_(None), Grant.user_id == user_id),
        )
    )
    return {p for row in grants for p in row.permissions}


async def require_grants(session, identity):
    granted = await granted_scopes(
        session, identity.tenant_id, identity.client_id, identity.user_id, identity.resource
    )
    if not set(identity.scopes) - OIDC <= granted:
        raise OAuthError("invalid_grant", "Required consent is no longer present")
