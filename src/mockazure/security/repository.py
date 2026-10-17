"""Canonical object reads: normalized identity rows remain the authentication authority."""

from uuid import UUID, uuid5

from sqlalchemy import String, case, cast, column, func, literal, select, text, true, union_all
from sqlalchemy.dialects.postgresql import JSONB, array, insert

from mockazure.identity.models import Application, Credential, ServicePrincipal
from mockazure.managed_identity.models import ManagedIdentity
from mockazure.providers.graph.errors import GraphError
from mockazure.security.catalog import permission_catalog, resource_app_id
from mockazure.security.credentials import owner_condition
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
            Application.deleted_at.is_(None),
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
            .on_conflict_do_update(
                index_elements=[SecurityObject.tenant_id, SecurityObject.id],
                set_={"properties": properties},
            )
        )


def document(**values):
    arguments = []
    for key, value in values.items():
        arguments.extend([key, value])
    return cast(func.jsonb_build_object(*arguments), JSONB)


def credential_array(row, tenant, kind, application):
    owner = row.object_id if application else row.id

    def time_string(column):
        return func.to_char(func.timezone("UTC", column), 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')

    fields = dict(
        keyId=cast(Credential.id, String),
        displayName=Credential.display_name,
        startDateTime=time_string(Credential.starts_at),
        endDateTime=time_string(Credential.expires_at),
    )
    if kind == "certificate":
        fields.update(
            type="AsymmetricX509Cert",
            usage="Verify",
            key=func.regexp_replace(Credential.public_certificate, r"-----[^-]+-----|\s", "", "g"),
        )
    return func.coalesce(
        select(func.jsonb_agg(document(**fields)))
        .where(
            Credential.tenant_id == tenant,
            Credential.client_id == row.client_id,
            Credential.kind == kind,
            Credential.revoked_at.is_(None),
            owner_condition(owner, application),
        )
        .correlate(row)
        .scalar_subquery(),
        literal([], JSONB),
    )


def required_access(row):
    entries = (
        func.jsonb_array_elements(row.required_permissions)
        .table_valued(column("value", JSONB))
        .alias("required_entry")
    )
    names = (
        func.jsonb_array_elements_text(entries.c.value["permissions"])
        .table_valued(column("value", String))
        .alias("required_name")
    )
    mapping = {
        kind: {
            entry["name"]: entry["id"]
            for entry in permission_catalog()["permissions"]
            if entry["kind"] == kind
        }
        for kind in ("application", "delegated")
    }
    identifier = (
        literal(mapping, JSONB)
        .op("->")(entries.c.value["kind"].as_string())
        .op("->>")(names.c.value)
    )
    access = (
        select(
            func.jsonb_agg(
                document(
                    id=identifier,
                    type=case(
                        (entries.c.value["kind"].as_string() == "application", "Role"),
                        else_="Scope",
                    ),
                )
            )
        )
        .select_from(entries)
        .join(names, true())
        .where(
            entries.c.value["resource"].as_string() == "graph",
            identifier.is_not(None),
        )
        .correlate(row)
        .scalar_subquery()
    )
    return case(
        (
            access.is_not(None),
            func.jsonb_build_array(
                document(
                    resourceAppId=str(resource_app_id("graph")),
                    resourceAccess=access,
                )
            ),
        ),
        else_=literal([], JSONB),
    )


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
            .op("||")(
                case(
                    (row.profile.has_any(array(["web", "publicClient", "spa"])), row.profile),
                    else_=row.profile.op("||")(
                        case(
                            (
                                row.client_mode == "public",
                                document(publicClient=document(redirectUris=row.redirect_uris)),
                            ),
                            else_=document(web=document(redirectUris=row.redirect_uris)),
                        )
                    ),
                )
            )
            .op("||")(
                document(
                    id=cast(row.object_id, String),
                    appId=cast(row.client_id, String),
                    displayName=row.display_name,
                    requiredResourceAccess=required_access(row),
                    passwordCredentials=credential_array(row, tenant, "secret", True),
                    keyCredentials=credential_array(row, tenant, "certificate", True),
                )
            )
        )
        return select(row.object_id.label("id"), body.label("payload")).where(
            row.tenant_id == tenant, row.deleted_at.is_(None)
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
                    displayName=func.coalesce(
                        row.profile["displayName"].as_string(), Application.display_name
                    ),
                    passwordCredentials=credential_array(row, tenant, "secret", False),
                    keyCredentials=credential_array(row, tenant, "certificate", False),
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
            .where(row.tenant_id == tenant, row.deleted_at.is_(None))
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
    key = "deviceId" if kind == "device" else "appId"
    column = source.c.payload[key].as_string() if by_appid else source.c.id
    result = await session.scalar(
        select(source.c.payload).where(column == (str(value) if by_appid else value))
    )
    if result is None:
        missing()
    return result
