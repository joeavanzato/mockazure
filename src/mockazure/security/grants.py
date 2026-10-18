"""Graph views and mutations of the grants consumed by the local OAuth issuer."""

from hashlib import md5
from uuid import UUID, uuid4, uuid5

from sqlalchemy import (
    String,
    Uuid,
    case,
    cast,
    column,
    delete,
    func,
    literal,
    select,
    true,
    union_all,
)
from sqlalchemy.dialects.postgresql import JSONB

from mockazure.authorization.catalog import permission
from mockazure.authorization.evaluator import OperationRequirement
from mockazure.identity.models import Application, DirectoryRole, Grant, ServicePrincipal, User
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.audit import record_security_audit
from mockazure.security.catalog import resource_permissions
from mockazure.security.models import GrantRoleMetadata, SecurityObject
from mockazure.security.mutations import invalid, uuid_value
from mockazure.security.permissions import actor_object_id, forbidden
from mockazure.security.repository import (
    document,
    ensure_resource_principals,
    lock_security,
    missing,
)

DELEGATED_ADMINS = {
    "Global Administrator",
    "Privileged Role Administrator",
    "Application Administrator",
    "Cloud Application Administrator",
    "Directory Writers",
    "User Administrator",
}
READ_ROLES = DELEGATED_ADMINS | {"Global Reader", "Directory Readers", "Application Developer"}


def resource_id(tenant, resource):
    return uuid5(tenant, "resource-service-principal:" + resource)


def role_map():
    return {
        resource: {
            entry["name"]: entry["id"]
            for entry in resource_permissions(resource)
            if entry["kind"] == "application"
        }
        for resource in ("graph", "exchange", "activity")
    }


def assignment_id(grant_id, name):
    # Opaque relationship identifier, not an API permission ID or security digest.
    return UUID(md5((str(grant_id) + ":" + name).encode(), usedforsecurity=False).hexdigest())


def grant_source(tenant, kind):
    sp = ServicePrincipal
    names = (
        func.jsonb_array_elements_text(Grant.permissions)
        .table_valued(column("value", String))
        .alias("grant_permission")
    )
    resource = case(
        *[
            (Grant.resource == name, str(resource_id(tenant, name)))
            for name in ("graph", "exchange", "arm", "activity")
        ]
    )
    if kind == "oAuth2PermissionGrant":
        identifier = Grant.id
        scope = (
            select(func.string_agg(names.c.value, " "))
            .select_from(names)
            .correlate(Grant)
            .scalar_subquery()
        )
        payload = document(
            id=cast(identifier, String),
            clientId=cast(sp.id, String),
            consentType=case((Grant.user_id.is_(None), "AllPrincipals"), else_="Principal"),
            principalId=cast(Grant.user_id, String),
            resourceId=resource,
            scope=func.coalesce(scope, ""),
        )
        statement = select(
            identifier.label("id"), Grant.id.label("grant_id"), payload.label("payload")
        )
        grant_kind = "delegated"
    else:
        identifier = cast(func.md5(cast(Grant.id, String) + ":" + names.c.value), Uuid)
        permission_id = literal(role_map(), JSONB).op("->")(Grant.resource).op("->>")(names.c.value)
        payload = document(
            id=cast(identifier, String),
            principalId=cast(sp.id, String),
            principalType="ServicePrincipal",
            principalDisplayName=func.coalesce(
                sp.profile["displayName"].as_string(), Application.display_name
            ),
            resourceId=resource,
            appRoleId=permission_id,
            resourceDisplayName=case(
                (Grant.resource == "graph", "Microsoft Graph"),
                (Grant.resource == "exchange", "Office 365 Exchange Online"),
                (Grant.resource == "activity", "Office 365 Management APIs"),
            ),
            createdDateTime=select(GrantRoleMetadata.created_at)
            .where(
                GrantRoleMetadata.grant_id == Grant.id,
                GrantRoleMetadata.permission == names.c.value,
            )
            .correlate(Grant, names)
            .scalar_subquery(),
        )
        statement = select(
            identifier.label("id"),
            Grant.id.label("grant_id"),
            names.c.value.label("permission"),
            payload.label("payload"),
        )
        statement = statement.join(names, true()).where(permission_id.is_not(None))
        grant_kind = "application"
    canonical = (
        statement.select_from(Grant)
        .join(sp, (sp.tenant_id == Grant.tenant_id) & (sp.client_id == Grant.client_id))
        .join(Application, Application.client_id == sp.client_id)
        .where(
            Grant.tenant_id == tenant,
            Grant.kind == grant_kind,
            sp.deleted_at.is_(None),
            Application.deleted_at.is_(None),
        )
        .subquery()
    )
    if kind == "oAuth2PermissionGrant":
        return canonical
    from mockazure.directory.models import Group
    from mockazure.security.repository import security_source

    principals = union_all(
        select(cast(User.id, String)).where(User.tenant_id == tenant, User.deleted_at.is_(None)),
        select(cast(Group.id, String)).where(Group.tenant_id == tenant, Group.deleted_at.is_(None)),
    )
    resources = security_source(tenant, "servicePrincipal")
    supplemental = select(
        SecurityObject.id.label("id"),
        literal(None, Uuid).label("grant_id"),
        literal(None, String).label("permission"),
        SecurityObject.properties.label("payload"),
    ).where(
        SecurityObject.tenant_id == tenant,
        SecurityObject.kind == "principalAppRoleAssignment",
        SecurityObject.properties["principalId"].as_string().in_(principals),
        SecurityObject.properties["resourceId"]
        .as_string()
        .in_(select(cast(resources.c.id, String))),
    )
    return union_all(select(canonical), supplemental).subquery()


async def grant_item(session, tenant, kind, identifier):
    source = grant_source(tenant, kind)
    row = (
        (await session.execute(select(source).where(source.c.id == uuid_value(identifier))))
        .mappings()
        .first()
    )
    if row is None:
        missing()
    return row


async def read_grants(session, actor, kind):
    names = (
        {"Directory.Read.All", "Directory.ReadWrite.All", "DelegatedPermissionGrant.ReadWrite.All"}
        if kind == "oAuth2PermissionGrant"
        else {
            "Application.Read.All",
            "Application.ReadWrite.All",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
        }
    )
    await permitted(
        actor,
        OperationRequirement("graph", frozenset(names), frozenset(names), frozenset(READ_ROLES)),
        session,
    )


async def client_principal(session, tenant, identifier):
    row = await session.get(ServicePrincipal, uuid_value(identifier))
    if row is None or row.tenant_id != tenant or row.deleted_at is not None:
        missing()
    app = await session.get(Application, row.client_id)
    if app is None or app.deleted_at is not None:
        missing()
    return row


def resource_name(tenant, identifier):
    identifier = uuid_value(identifier)
    for name in ("graph", "exchange", "arm", "activity"):
        if identifier == resource_id(tenant, name):
            return name
    invalid("The resource must be a declared tenant-local API service principal")


async def authorize_grant(session, actor, kind, operation, resource, body, client):
    if kind == "oAuth2PermissionGrant":
        names = {"DelegatedPermissionGrant.ReadWrite.All", "Directory.ReadWrite.All"}
        await permitted(
            actor, OperationRequirement("graph", frozenset(names), frozenset(names)), session
        )
    else:
        names = (
            {"AppRoleAssignment.ReadWrite.All", "Application.ReadWrite.All"}
            if operation == "delete"
            else {"AppRoleAssignment.ReadWrite.All"}
        )
        await permitted(
            actor, OperationRequirement("graph", frozenset(names), frozenset(names)), session
        )
        if operation != "delete" and not actor.permissions & {
            "Application.Read.All",
            "Application.ReadWrite.All",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
        }:
            forbidden()
    if actor.token_kind == "application":
        return
    roles = set(
        await session.scalars(
            select(DirectoryRole.role).where(
                DirectoryRole.tenant_id == actor.tenant_id,
                DirectoryRole.user_id == actor.subject_id,
            )
        )
    )
    allowed = (
        {"Global Administrator", "Privileged Role Administrator"}
        if kind == "appRoleAssignment" and resource == "graph"
        else DELEGATED_ADMINS
    )
    if roles & allowed:
        return
    # The Application Developer role does not grant tenant-wide administrator consent.
    if kind == "oAuth2PermissionGrant" and "Application Developer" in roles:
        from mockazure.security.models import SecurityOwner

        owned = await session.get(
            SecurityOwner, (actor.tenant_id, client.id, await actor_object_id(session, actor))
        )
        if (
            owned
            and body.get("principalId") == str(actor.subject_id)
            and all(
                not permission(resource, "delegated", name).admin_consent_required
                for name in body.get("scope", "").split()
            )
        ):
            return
    forbidden()


async def mutate_grant(session, actor, kind, operation, identifier, body, now):
    await lock_security(session, actor.tenant_id)
    await ensure_resource_principals(session, actor.tenant_id)
    before, row, assigned_name = None, None, None
    if identifier is not None:
        view = await grant_item(session, actor.tenant_id, kind, identifier)
        before = view["payload"]
        if view["grant_id"] is None:
            await read_grants(session, actor, kind)
            invalid("Provisioned user/group assignments are read-only in the public profile")
        row = await session.get(Grant, view["grant_id"])
        assigned_name = view.get("permission")
        resource = row.resource
        client = await client_principal(
            session,
            actor.tenant_id,
            before["clientId"] if kind == "oAuth2PermissionGrant" else before["principalId"],
        )
    else:
        if not isinstance(body, dict):
            invalid()
        expected = (
            {"clientId", "resourceId", "consentType", "scope", "principalId"}
            if kind == "oAuth2PermissionGrant"
            else {"principalId", "resourceId", "appRoleId"}
        )
        required = expected - ({"principalId"} if kind == "oAuth2PermissionGrant" else set())
        if set(body) - expected or not required <= body.keys():
            invalid("Grant properties are missing or unsupported")
        try:
            validate_payload("v1.0", kind, body, partial=True)
        except ValueError:
            invalid("Grant does not match the official Graph schema")
        client = await client_principal(
            session,
            actor.tenant_id,
            body["clientId"] if kind == "oAuth2PermissionGrant" else body["principalId"],
        )
        resource = resource_name(actor.tenant_id, body["resourceId"])
    if operation == "update" and (kind != "oAuth2PermissionGrant" or set(body) != {"scope"}):
        invalid("Only delegated scope can be updated")
    effective = dict(before or {}) | body
    scopes, subject = None, None
    if kind == "oAuth2PermissionGrant" and operation != "delete":
        raw = effective["scope"]
        if not isinstance(raw, str) or len(raw) > 3850:
            invalid("Delegated scope must be a bounded space-separated string")
        scopes = sorted(set(raw.split()))
        try:
            for name in scopes:
                permission(resource, "delegated", name)
        except ValueError:
            invalid("Unknown delegated scope for resource")
        consent_type = effective["consentType"]
        if consent_type == "Principal":
            subject = uuid_value(effective.get("principalId"))
            user = await session.get(User, subject)
            if user is None or user.tenant_id != actor.tenant_id or user.deleted_at is not None:
                missing()
        elif consent_type != "AllPrincipals" or effective.get("principalId") is not None:
            invalid("AllPrincipals requires a null principalId")
    elif kind == "appRoleAssignment" and operation != "delete":
        identifier_value = str(uuid_value(effective["appRoleId"]))
        assigned_name = next(
            (
                name
                for name, value in role_map().get(resource, {}).items()
                if value == identifier_value
            ),
            None,
        )
        if assigned_name is None:
            invalid("Unknown application permission for resource")
    await authorize_grant(session, actor, kind, operation, resource, effective, client)
    if operation == "create":
        existing = await session.scalar(
            select(Grant).where(
                Grant.tenant_id == actor.tenant_id,
                Grant.client_id == client.client_id,
                Grant.resource == resource,
                Grant.kind == ("delegated" if kind == "oAuth2PermissionGrant" else "application"),
                Grant.user_id.is_(None) if subject is None else Grant.user_id == subject,
            )
        )
        if existing is not None:
            if kind == "oAuth2PermissionGrant" or assigned_name in existing.permissions:
                raise GraphError(409, "Request_Conflict", "Permission grant already exists")
            row = existing
        else:
            row = Grant(
                id=uuid4(),
                tenant_id=actor.tenant_id,
                client_id=client.client_id,
                resource=resource,
                kind="delegated" if kind == "oAuth2PermissionGrant" else "application",
                user_id=subject,
                permissions=[],
                created_at=now,
            )
            session.add(row)
    if operation == "delete":
        if kind == "oAuth2PermissionGrant":
            await session.delete(row)
        else:
            await session.execute(
                delete(GrantRoleMetadata).where(
                    GrantRoleMetadata.grant_id == row.id,
                    GrantRoleMetadata.permission == assigned_name,
                )
            )
            row.permissions = sorted(set(row.permissions) - {assigned_name})
            if not row.permissions:
                await session.delete(row)
    elif kind == "oAuth2PermissionGrant":
        row.permissions = scopes
    else:
        row.permissions = sorted(set(row.permissions) | {assigned_name})
    await session.flush()
    if kind == "appRoleAssignment" and operation == "create":
        metadata = await session.get(GrantRoleMetadata, (row.id, assigned_name))
        if metadata is None:
            metadata = GrantRoleMetadata(grant_id=row.id, permission=assigned_name, created_at=now)
            session.add(metadata)
        else:
            metadata.created_at = now
        await session.flush()
    identifier = row.id if kind == "oAuth2PermissionGrant" else assignment_id(row.id, assigned_name)
    after = (
        None
        if operation == "delete"
        else (await grant_item(session, actor.tenant_id, kind, identifier))["payload"]
    )
    await record_security_audit(
        session,
        actor,
        {"create": "Add", "update": "Update", "delete": "Remove"}[operation] + " " + kind,
        {"id": str(identifier), "type": kind, "displayName": None},
        before,
        after,
        now,
    )
    return after if operation == "create" else None
