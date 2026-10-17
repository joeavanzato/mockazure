"""Canonical object reads: normalized identity rows remain the authentication authority."""

from uuid import UUID, uuid5

from sqlalchemy import String, cast, func, literal, select, text, union_all
from sqlalchemy.dialects.postgresql import JSONB, insert

from mockazure.identity.models import Application, ServicePrincipal
from mockazure.managed_identity.models import ManagedIdentity
from mockazure.providers.graph.errors import GraphError
from mockazure.security.catalog import permission_catalog, resource_app_id
from mockazure.security.models import SecurityObject


async def lock_security(session, tenant_id):
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant, 389173))"),
        {"tenant": str(tenant_id)},
    )


def missing():
    raise GraphError(404, "Request_ResourceNotFound", "Security object was not found")


async def application_object(session, tenant, id_or_appid, *, by_appid=False):
    try:
        identifier = UUID(str(id_or_appid))
    except ValueError:
        missing()
    column = Application.client_id if by_appid else Application.object_id
    row = await session.scalar(
        select(Application).where(
            Application.tenant_id == tenant,
            column == identifier,
        )
    )
    if row is None:
        missing()
    return row


async def ensure_resource_principals(session, tenant):
    for resource, name in [
        ("graph", "Microsoft Graph"),
        ("exchange", "Office 365 Exchange Online"),
        ("arm", "Windows Azure Service Management API"),
    ]:
        identifier = uuid5(tenant, "resource-service-principal:" + resource)
        properties = {
            "id": str(identifier),
            "appId": str(resource_app_id(resource)),
            "displayName": name,
            "servicePrincipalType": "Application",
            "accountEnabled": True,
            "appRoles": [],
            "oauth2PermissionScopes": [],
            "tags": [],
        }
        if resource == "graph":
            for entry in permission_catalog()["permissions"]:
                value = {"id": entry["id"], "value": entry["name"], "isEnabled": True}
                if entry["kind"] == "application":
                    properties["appRoles"].append(
                        value
                        | {
                            "displayName": entry["name"],
                            "description": entry["name"],
                            "allowedMemberTypes": ["Application"],
                        }
                    )
                else:
                    properties["oauth2PermissionScopes"].append(
                        value
                        | {
                            "adminConsentDisplayName": entry["name"],
                            "adminConsentDescription": entry["name"],
                            "type": "Admin" if entry["adminConsentRequired"] else "User",
                        }
                    )
        await session.execute(
            insert(SecurityObject)
            .values(
                tenant_id=tenant,
                id=identifier,
                kind="resourceServicePrincipal",
                properties=properties,
            )
            .on_conflict_do_nothing()
        )


def document(**values):
    arguments = []
    for key, value in values.items():
        arguments.extend([key, value])
    return cast(func.jsonb_build_object(*arguments), JSONB)


def identity_source(tenant, kind):
    if kind == "application":
        row = Application
        body = (
            literal(
                {
                    "@odata.type": "#microsoft.graph.application",
                    "signInAudience": "AzureADMyOrg",
                    "identifierUris": [],
                    "tags": [],
                    "passwordCredentials": [],
                    "keyCredentials": [],
                },
                JSONB,
            )
            .op("||")(row.profile)
            .op("||")(
                document(
                    id=cast(row.object_id, String),
                    appId=cast(row.client_id, String),
                    displayName=row.display_name,
                )
            )
        )
        return select(row.object_id.label("id"), body.label("payload")).where(
            row.tenant_id == tenant
        )
    if kind == "servicePrincipal":
        row = ServicePrincipal
        body = (
            literal(
                {
                    "@odata.type": "#microsoft.graph.servicePrincipal",
                    "tags": [],
                    "passwordCredentials": [],
                    "keyCredentials": [],
                    "appRoles": [],
                    "oauth2PermissionScopes": [],
                },
                JSONB,
            )
            .op("||")(row.profile)
            .op("||")(
                document(
                    id=cast(row.id, String),
                    appId=cast(row.client_id, String),
                    displayName=Application.display_name,
                    accountEnabled=row.enabled,
                    servicePrincipalType=func.coalesce(
                        select(literal("ManagedIdentity"))
                        .where(
                            ManagedIdentity.tenant_id == tenant,
                            ManagedIdentity.id == row.id,
                        )
                        .correlate(row)
                        .scalar_subquery(),
                        literal("Application"),
                    ),
                )
            )
        )
        return (
            select(row.id.label("id"), body.label("payload"))
            .join(
                Application,
                Application.client_id == row.client_id,
            )
            .where(row.tenant_id == tenant)
        )
    return None


def security_source(tenant, kind):
    canonical = identity_source(tenant, kind)
    if kind == "application":
        return canonical.subquery()
    stored_kind = "resourceServicePrincipal" if kind == "servicePrincipal" else kind
    body = SecurityObject.properties.op("||")(
        document(
            id=cast(SecurityObject.id, String),
            **{"@odata.type": "#microsoft.graph." + kind},
        )
    )
    supplementary = select(SecurityObject.id.label("id"), body.label("payload")).where(
        SecurityObject.tenant_id == tenant,
        SecurityObject.kind == stored_kind,
    )
    return (
        union_all(canonical, supplementary) if canonical is not None else supplementary
    ).subquery()


async def public_security_object(session, tenant, kind, identifier, *, by_appid=False):
    source = security_source(tenant, kind)
    try:
        value = UUID(str(identifier))
    except ValueError:
        missing()
    column = source.c.payload["appId"].as_string() if by_appid else source.c.id
    result = await session.scalar(
        select(source.c.payload).where(column == (str(value) if by_appid else value))
    )
    if result is None:
        missing()
    return result
