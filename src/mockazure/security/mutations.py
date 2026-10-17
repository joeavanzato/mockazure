"""Tenant-serialized security mutations update the same rows used by OAuth."""

from uuid import UUID, uuid4

from sqlalchemy import delete, select

from mockazure.identity.models import Application, ServicePrincipal, User
from mockazure.identity.types import validate_redirect_uri
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.audit import record_security_audit
from mockazure.security.catalog import resource_app_id, resource_permissions
from mockazure.security.credentials import add_password, remove_password, replace_certificates
from mockazure.security.models import SecurityObject, SecurityOwner
from mockazure.security.permissions import actor_object_id, authorize_mutation
from mockazure.security.repository import (
    application_object,
    lock_security,
    missing,
    public_security_object,
)

APP_FIELDS = {
    "displayName",
    "description",
    "tags",
    "identifierUris",
    "signInAudience",
    "requiredResourceAccess",
    "web",
    "publicClient",
    "spa",
    "keyCredentials",
}
SP_FIELDS = {
    "displayName",
    "description",
    "tags",
    "accountEnabled",
    "keyCredentials",
}
DEVICE_FIELDS = {
    "displayName",
    "accountEnabled",
    "operatingSystem",
    "operatingSystemVersion",
    "deviceId",
    "trustType",
    "alternativeSecurityIds",
}


def invalid(message="Property is outside the supported security write contract"):
    raise GraphError(400, "Request_BadRequest", message)


def uuid_value(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        invalid("A valid identifier is required")


def configured_permissions(values):
    if not isinstance(values, list) or len(values) > 64:
        invalid()
    entries = {}
    for entry in values:
        if set(entry) != {"resourceAppId", "resourceAccess"}:
            invalid()
        resource = next(
            (
                name
                for name in ("graph", "exchange")
                if uuid_value(entry["resourceAppId"]) == resource_app_id(name)
            ),
            None,
        )
        if resource is None:
            invalid("Resource permission catalog is not supported")
        for access in entry["resourceAccess"]:
            if set(access) != {"id", "type"} or access["type"] not in {"Role", "Scope"}:
                invalid()
            kind = "application" if access["type"] == "Role" else "delegated"
            identifier = str(uuid_value(access["id"]))
            name = next(
                (
                    item["name"]
                    for item in resource_permissions(resource)
                    if item["id"] == identifier and item["kind"] == kind
                ),
                None,
            )
            if name is None:
                invalid("Unknown resource permission identifier")
            entries.setdefault((resource, kind), set()).add(name)
    return [
        {"resource": resource, "kind": kind, "permissions": sorted(names)}
        for (resource, kind), names in entries.items()
    ]


def validated(kind, operation, body):
    if not isinstance(body, dict):
        invalid()
    allowed = (
        APP_FIELDS
        if kind == "application"
        else SP_FIELDS
        if kind == "servicePrincipal"
        else DEVICE_FIELDS
    )
    if kind == "servicePrincipal" and operation == "create":
        allowed = (allowed - {"keyCredentials"}) | {"appId"}
    if set(body) - allowed:
        invalid()
    try:
        validate_payload("v1.0", kind, body, partial=True)
    except ValueError:
        invalid("Property does not match the official Graph structural schema")
    if "displayName" in body and (not body["displayName"] or len(body["displayName"]) > 200):
        invalid("Display name must contain 1 to 200 characters")
    if "accountEnabled" in body and type(body["accountEnabled"]) is not bool:
        invalid()
    if kind == "application" and body.get("signInAudience", "AzureADMyOrg") != "AzureADMyOrg":
        invalid("This local profile supports single-tenant applications")
    return dict(body)


async def object_row(session, tenant, kind, identifier):
    if kind == "application":
        return await application_object(session, tenant, identifier)
    row = (
        await session.get(ServicePrincipal, identifier)
        if kind == "servicePrincipal"
        else (await session.get(SecurityObject, (tenant, identifier)))
    )
    if row is None or row.tenant_id != tenant:
        missing()
    if kind == "servicePrincipal" and row.deleted_at is not None:
        missing()
    if isinstance(row, SecurityObject) and row.kind != kind:
        missing()
    return row


async def owner_public(session, tenant, identifier):
    user = await session.get(User, identifier)
    if user and user.tenant_id == tenant and user.deleted_at is None:
        return {
            "@odata.type": "#microsoft.graph.user",
            "id": str(user.id),
            "displayName": user.display_name,
            "userPrincipalName": user.upn,
        }
    from mockazure.providers.graph.security.serialization import serialize_security

    return serialize_security(
        await public_security_object(session, tenant, "servicePrincipal", identifier)
    )


async def owner_list(session, tenant, identifier):
    identifiers = list(
        await session.scalars(
            select(SecurityOwner.owner_id)
            .where(
                SecurityOwner.tenant_id == tenant,
                SecurityOwner.object_id == identifier,
            )
            .order_by(SecurityOwner.owner_id)
            .limit(101)
        )
    )
    if len(identifiers) > 100:
        invalid("Owner expansion exceeds the supported 100-owner bound")
    return [await owner_public(session, tenant, value) for value in identifiers]


async def mutate_security(session, actor, kind, operation, identifier, body, now):
    await lock_security(session, actor.tenant_id)
    identifier = uuid_value(identifier) if identifier is not None else None
    before, row = None, None
    if identifier is not None:
        row = await object_row(session, actor.tenant_id, kind, identifier)
        before = await public_security_object(session, actor.tenant_id, kind, identifier)
    authorization_target = identifier
    if operation == "create" and kind == "servicePrincipal":
        backing = await application_object(
            session, actor.tenant_id, body.get("appId"), by_appid=True
        )
        authorization_target = backing.object_id
    await authorize_mutation(session, actor, kind, operation, authorization_target, body=body)
    result = None
    if operation in {"create", "update"}:
        data = validated(kind, operation, body)
        if operation == "create":
            if kind == "application":
                if not data.get("displayName"):
                    invalid("displayName is required")
                row = Application(
                    tenant_id=actor.tenant_id,
                    display_name=data["displayName"],
                    client_mode="confidential",
                    profile={"createdDateTime": now.isoformat()},
                    redirect_uris=[],
                    required_permissions=[],
                )
                session.add(row)
                await session.flush()
                identifier = row.object_id
            elif kind == "servicePrincipal":
                existing = await session.scalar(
                    select(ServicePrincipal.id).where(
                        ServicePrincipal.tenant_id == actor.tenant_id,
                        ServicePrincipal.client_id == backing.client_id,
                    )
                )
                if existing:
                    raise GraphError(
                        409,
                        "Request_MultipleObjectsWithSameKeyValue",
                        "A service principal already exists for the application",
                    )
                row = ServicePrincipal(
                    tenant_id=actor.tenant_id, client_id=backing.client_id, profile={}
                )
                session.add(row)
                await session.flush()
                identifier = row.id
            else:
                if not data.get("displayName"):
                    invalid("displayName is required")
                data.setdefault("deviceId", str(uuid4()))
                data["deviceId"] = str(uuid_value(data["deviceId"]))
                row = SecurityObject(tenant_id=actor.tenant_id, kind="device", properties={})
                session.add(row)
                await session.flush()
                identifier = row.id
            if kind != "device":
                session.add(
                    SecurityOwner(
                        tenant_id=actor.tenant_id,
                        object_id=identifier,
                        owner_id=await actor_object_id(session, actor),
                    )
                )
        if kind == "application":
            if "displayName" in data:
                row.display_name = data["displayName"]
            if "requiredResourceAccess" in data:
                row.required_permissions = configured_permissions(data["requiredResourceAccess"])
            profile = dict(row.profile)
            if not profile.keys() & {"web", "publicClient", "spa"}:
                platform = "publicClient" if row.client_mode == "public" else "web"
                profile[platform] = {"redirectUris": list(row.redirect_uris)}
            profile.update({k: v for k, v in data.items() if k != "keyCredentials"})
            if any(key in data for key in ("web", "publicClient", "spa")):
                redirect_uris = []
                for name in ("web", "publicClient", "spa"):
                    section = profile.get(name) or {}
                    if set(section) - {"redirectUris"}:
                        invalid("Only redirectUris is supported for this application platform")
                    try:
                        redirect_uris.extend(
                            validate_redirect_uri(uri) for uri in section.get("redirectUris", [])
                        )
                    except ValueError:
                        invalid("Redirect URIs must remain local")
                if len(redirect_uris) > 32:
                    invalid()
                row.redirect_uris = list(dict.fromkeys(redirect_uris))
                row.client_mode = (
                    "public"
                    if profile.get("publicClient", {}).get("redirectUris")
                    else "confidential"
                )
            row.profile = profile
        elif kind == "servicePrincipal":
            if "accountEnabled" in data:
                row.enabled = data["accountEnabled"]
            row.profile = dict(row.profile) | {
                k: v for k, v in data.items() if k not in {"keyCredentials", "appId"}
            }
        else:
            if (
                operation == "update"
                and actor.token_kind == "application"
                and row.properties.get("operatingSystem") != "Windows"
            ):
                invalid("Application updates to non-Windows devices require extensionAttributes")
            if operation == "update" and "deviceId" in data:
                invalid("deviceId cannot be updated")
            row.properties = dict(row.properties) | data
        if "keyCredentials" in data:
            await replace_certificates(
                session,
                actor.tenant_id,
                row.client_id,
                identifier,
                data["keyCredentials"],
                now,
                application=kind == "application",
            )
    elif operation in {"add_password", "remove_password"}:
        if kind not in {"application", "servicePrincipal"}:
            invalid()
        if operation == "add_password":
            result = await add_password(
                session, actor.tenant_id, row.client_id, identifier, body, now
            )
        else:
            await remove_password(
                session,
                actor.tenant_id,
                row.client_id,
                identifier,
                body,
                now,
                application=kind == "application",
            )
    elif operation in {"add_owner", "remove_owner"}:
        owner_id = uuid_value(body["owner_id"])
        owner_value = await owner_public(session, actor.tenant_id, owner_id)
        if kind == "device" and owner_value["@odata.type"] != "#microsoft.graph.user":
            invalid("A registered device owner must be a user")
        before = dict(before) | {"owners": await owner_list(session, actor.tenant_id, identifier)}
        owner = await session.get(SecurityOwner, (actor.tenant_id, identifier, owner_id))
        if operation == "add_owner":
            if owner is not None:
                invalid("Owner reference already exists")
            if len(before["owners"]) >= 100:
                invalid("Owner count exceeds the supported bound")
            session.add(
                SecurityOwner(tenant_id=actor.tenant_id, object_id=identifier, owner_id=owner_id)
            )
        elif owner is None:
            missing()
        else:
            await session.delete(owner)
    elif operation == "delete":
        if kind in {"application", "servicePrincipal"}:
            row.deleted_at, row.enabled = now, False
            if kind == "application":
                for sp in await session.scalars(
                    select(ServicePrincipal).where(
                        ServicePrincipal.tenant_id == actor.tenant_id,
                        ServicePrincipal.client_id == row.client_id,
                    )
                ):
                    sp.deleted_at, sp.enabled = now, False
        else:
            await session.delete(row)
        await session.execute(
            delete(SecurityOwner).where(
                SecurityOwner.tenant_id == actor.tenant_id, SecurityOwner.object_id == identifier
            )
        )
    else:
        invalid()
    await session.flush()
    after = (
        None
        if operation == "delete"
        else await public_security_object(session, actor.tenant_id, kind, identifier)
    )
    if operation in {"add_owner", "remove_owner"}:
        after["owners"] = await owner_list(session, actor.tenant_id, identifier)
    label = {
        "application": "application",
        "servicePrincipal": "service principal",
        "device": "device",
    }[kind]
    activity = {
        "create": "Add",
        "update": "Update",
        "delete": "Delete",
        "add_password": "Add password to",
        "remove_password": "Remove password from",
        "add_owner": "Add owner to",
        "remove_owner": "Remove owner from",
    }[operation]
    target = {
        "id": str(identifier),
        "type": kind[0].upper() + kind[1:],
        "displayName": (after or before).get("displayName"),
    }
    await record_security_audit(session, actor, activity + " " + label, target, before, after, now)
    return result if result is not None else after if operation == "create" else None
