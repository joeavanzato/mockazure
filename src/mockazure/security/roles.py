"""Built-in role identity views backed by effective user and service-principal roles."""

import json
from functools import lru_cache
from hashlib import md5
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import String, Uuid, cast, column, func, literal, select, union_all
from sqlalchemy.dialects.postgresql import JSONB, insert

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.identity.models import (
    Application,
    DirectoryRole,
    ServicePrincipal,
    ServicePrincipalRole,
    User,
)
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.security.audit import record_security_audit
from mockazure.security.models import RoleActivation
from mockazure.security.mutations import invalid, uuid_value
from mockazure.security.repository import document, lock_security, missing


@lru_cache(maxsize=1)
def role_ids():
    return json.loads((Path(__file__).parent / "data" / "role_templates.json").read_text("utf-8"))[
        "roles"
    ]


@lru_cache(maxsize=1)
def role_details():
    return json.loads((Path(__file__).parent / "data" / "role_details.json").read_text("utf-8"))[
        "roles"
    ]


def role_definitions():
    return [
        {
            "id": identifier,
            "templateId": identifier,
            "displayName": name,
            "isBuiltIn": True,
            "isEnabled": True,
            **role_details()[identifier],
        }
        for name, identifier in sorted(role_ids().items())
    ]


def legacy_id(tenant, name):
    return uuid5(tenant, "directory-role:" + role_ids()[name])


def assignment_id(tenant, kind, principal_id, definition_id):
    value = f"role:{tenant}:{kind}:{principal_id}:{definition_id}"
    return UUID(md5(value.encode(), usedforsecurity=False).hexdigest())


def definition_name(identifier):
    value = str(uuid_value(identifier))
    name = next((name for name, template in role_ids().items() if template == value), None)
    if name is None:
        missing()
    return name


async def activate_role(session, tenant, name):
    await session.execute(
        insert(RoleActivation).values(tenant_id=tenant, role=name).on_conflict_do_nothing()
    )


async def sync_activation(session, tenant):
    # Also covers migrated/bootstrap callers without inventing Graph-originated activity.
    assigned = union_all(
        select(DirectoryRole.role).where(DirectoryRole.tenant_id == tenant),
        select(ServicePrincipalRole.role).where(ServicePrincipalRole.tenant_id == tenant),
    ).subquery()
    await session.execute(
        insert(RoleActivation)
        .from_select(["tenant_id", "role"], select(literal(tenant), assigned.c.role).distinct())
        .on_conflict_do_nothing()
    )


def catalog_source(tenant):
    rows = (
        func.jsonb_each_text(literal(role_ids(), JSONB))
        .table_valued(column("key", String), column("value", String))
        .alias("role_templates")
    )
    legacy = literal({name: str(legacy_id(tenant, name)) for name in role_ids()}, JSONB)
    return select(
        rows.c.key.label("name"),
        cast(rows.c.value, Uuid).label("definition_id"),
        cast(legacy.op("->>")(rows.c.key), Uuid).label("legacy_id"),
    ).subquery()


def definition_payload(catalog):
    details = literal(role_details(), JSONB).op("->")(cast(catalog.c.definition_id, String))
    return document(
        id=cast(catalog.c.definition_id, String),
        templateId=cast(catalog.c.definition_id, String),
        displayName=catalog.c.name,
        isBuiltIn=True,
        isEnabled=True,
        description=details["description"].as_string(),
        rolePermissions=details["rolePermissions"],
    )


def role_source(tenant, kind):
    catalog = catalog_source(tenant)
    if kind == "unifiedRoleDefinition":
        return select(
            catalog.c.definition_id.label("id"), definition_payload(catalog).label("payload")
        ).subquery()
    return (
        select(
            catalog.c.legacy_id.label("id"),
            document(
                id=cast(catalog.c.legacy_id, String),
                displayName=catalog.c.name,
                roleTemplateId=cast(catalog.c.definition_id, String),
            ).label("payload"),
        )
        .join(
            RoleActivation,
            (RoleActivation.tenant_id == tenant) & (RoleActivation.role == catalog.c.name),
        )
        .subquery()
    )


def role_assignments_source(tenant, actor=None):
    catalog = catalog_source(tenant)
    statements = []
    for kind, role_model, principal_model, key in (
        ("user", DirectoryRole, User, DirectoryRole.user_id),
        (
            "servicePrincipal",
            ServicePrincipalRole,
            ServicePrincipal,
            ServicePrincipalRole.client_id,
        ),
    ):
        principal_key = User.id if kind == "user" else ServicePrincipal.client_id
        principal_id = principal_model.id
        identifier = cast(
            func.md5(
                "role:"
                + str(tenant)
                + ":"
                + kind
                + ":"
                + cast(principal_id, String)
                + ":"
                + cast(catalog.c.definition_id, String)
            ),
            Uuid,
        )
        payload = document(
            id=cast(identifier, String),
            principalId=cast(principal_id, String),
            roleDefinitionId=cast(catalog.c.definition_id, String),
            directoryScopeId="/",
            appScopeId=None,
        )
        disclosure = {"id": cast(principal_id, String), "@odata.type": "#microsoft.graph." + kind}
        permissions = actor.permissions if actor else set()
        if kind == "user" and permissions & {
            "User.Read.All",
            "User.ReadBasic.All",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
        }:
            disclosure.update(displayName=User.display_name, userPrincipalName=User.upn)
        if kind == "servicePrincipal" and permissions & {
            "Application.Read.All",
            "Application.ReadWrite.All",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
        }:
            disclosure.update(
                displayName=func.coalesce(
                    ServicePrincipal.profile["displayName"].as_string(), Application.display_name
                ),
                appId=cast(ServicePrincipal.client_id, String),
            )
        statement = (
            select(
                identifier.label("id"),
                payload.label("payload"),
                principal_id.label("principal_id"),
                catalog.c.name.label("role_name"),
                literal(kind).label("subject_kind"),
                document(**disclosure).label("principal_payload"),
                definition_payload(catalog).label("role_payload"),
            )
            .select_from(role_model)
            .join(
                principal_model,
                (principal_model.tenant_id == role_model.tenant_id) & (principal_key == key),
            )
            .join(catalog, catalog.c.name == role_model.role)
            .where(role_model.tenant_id == tenant, principal_model.deleted_at.is_(None))
        )
        if kind == "servicePrincipal":
            statement = statement.join(
                Application, Application.client_id == ServicePrincipal.client_id
            ).where(Application.deleted_at.is_(None))
        statements.append(statement)
    return union_all(*statements).subquery()


async def require_role_access(session, actor, *, write=False, legacy=False):
    names = (
        {"RoleManagement.ReadWrite.Directory"}
        if write
        else {
            "RoleManagement.Read.Directory",
            "RoleManagement.ReadWrite.Directory",
            "Directory.Read.All",
            "Directory.ReadWrite.All",
        }
    )
    roles = (
        {"Global Administrator", "Privileged Role Administrator"}
        if write
        else set()
        if legacy
        else {
            "Global Administrator",
            "Privileged Role Administrator",
            "Global Reader",
            "Directory Readers",
        }
    )
    await permitted(
        actor,
        OperationRequirement("graph", frozenset(names), frozenset(names), frozenset(roles)),
        session,
    )


async def assignment_view(session, tenant, identifier, actor=None):
    source = role_assignments_source(tenant, actor)
    row = (
        (await session.execute(select(source).where(source.c.id == uuid_value(identifier))))
        .mappings()
        .first()
    )
    if row is None:
        missing()
    return row


async def mutate_role_assignment(session, actor, identifier, body, now):
    await lock_security(session, actor.tenant_id)
    await require_role_access(session, actor, write=True)
    before = None
    if identifier is not None:
        view = await assignment_view(session, actor.tenant_id, identifier)
        before = view["payload"]
        name, subject_kind, principal_id = (
            view["role_name"],
            view["subject_kind"],
            view["principal_id"],
        )
    else:
        if (
            set(body) != {"principalId", "roleDefinitionId", "directoryScopeId"}
            or body["directoryScopeId"] != "/"
        ):
            invalid("Only built-in tenant-wide directory role assignments are supported")
        name = definition_name(body["roleDefinitionId"])
        principal_id = uuid_value(body["principalId"])
        user = await session.get(User, principal_id)
        if user and user.tenant_id == actor.tenant_id and user.deleted_at is None:
            subject_kind = "user"
        else:
            sp = await session.get(ServicePrincipal, principal_id)
            if not sp or sp.tenant_id != actor.tenant_id or sp.deleted_at is not None:
                missing()
            app = await session.get(Application, sp.client_id)
            if not app or app.deleted_at is not None:
                missing()
            subject_kind = "servicePrincipal"
    if subject_kind == "user":
        model, key = DirectoryRole, principal_id
        values = {"tenant_id": actor.tenant_id, "user_id": key, "role": name}
    else:
        sp = await session.get(ServicePrincipal, principal_id)
        model, key = ServicePrincipalRole, sp.client_id
        values = {"tenant_id": actor.tenant_id, "client_id": key, "role": name}
    existing = await session.get(model, (actor.tenant_id, key, name))
    if identifier is None:
        if existing is not None:
            raise GraphError(409, "Request_Conflict", "Directory role is already assigned")
        session.add(model(**values))
        await activate_role(session, actor.tenant_id, name)
    else:
        await session.delete(existing)
    await session.flush()
    assigned_id = assignment_id(actor.tenant_id, subject_kind, principal_id, role_ids()[name])
    after = (
        None
        if identifier is not None
        else (await assignment_view(session, actor.tenant_id, assigned_id))["payload"]
    )
    await record_security_audit(
        session,
        actor,
        "Remove directory role assignment"
        if identifier is not None
        else "Add directory role assignment",
        {"id": str(assigned_id), "type": "unifiedRoleAssignment", "displayName": name},
        before,
        after,
        now,
    )
    return after
