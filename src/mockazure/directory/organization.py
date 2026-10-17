"""Typed administrative collection state, separate from OAuth consent and roles."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import delete, select

from mockazure.db.models import Tenant
from mockazure.directory.models import Group
from mockazure.identity.models import User
from mockazure.providers.graph.body import invalid
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.models import DeviceUser, SecurityObject
from mockazure.security.repository import lock_security, missing, public_security_object

ORGANIZATION_FIELDS = frozenset(
    {
        "businessPhones",
        "city",
        "country",
        "countryLetterCode",
        "createdDateTime",
        "marketingNotificationEmails",
        "onPremisesLastSyncDateTime",
        "onPremisesSyncEnabled",
        "postalCode",
        "preferredLanguage",
        "privacyProfile",
        "securityComplianceNotificationMails",
        "securityComplianceNotificationPhones",
        "state",
        "street",
        "technicalNotificationMails",
    }
)
MAX_RECORDS = 1000


async def organization_profile(session, tenant_id):
    row = await session.get(SecurityObject, (tenant_id, uuid5(tenant_id, "organization-profile")))
    return dict(row.properties) if row is not None and row.kind == "organizationProfile" else {}


async def collection_state(session, tenant_id):
    if await session.get(Tenant, tenant_id) is None:
        missing()
    assignments = list(
        await session.scalars(
            select(SecurityObject.properties)
            .where(
                SecurityObject.tenant_id == tenant_id,
                SecurityObject.kind == "principalAppRoleAssignment",
            )
            .order_by(SecurityObject.id)
            .limit(MAX_RECORDS + 1)
        )
    )
    users = list(
        await session.scalars(
            select(DeviceUser)
            .where(DeviceUser.tenant_id == tenant_id)
            .order_by(DeviceUser.device_id, DeviceUser.user_id)
            .limit(MAX_RECORDS + 1)
        )
    )
    if len(assignments) > MAX_RECORDS or len(users) > MAX_RECORDS:
        invalid("Collection-state export exceeds the local bound")
    return {
        "organization": await organization_profile(session, tenant_id),
        "appRoleAssignments": assignments,
        "registeredUsers": [
            {"deviceId": str(x.device_id), "userId": str(x.user_id)} for x in users
        ],
    }


async def replace_collection_state(session, tenant_id, body):
    if set(body) - {"organization", "appRoleAssignments", "registeredUsers"} or not body:
        invalid("Unsupported collection-state fields")
    await lock_security(session, tenant_id)
    if await session.get(Tenant, tenant_id) is None:
        missing()
    if "organization" in body:
        values = body["organization"]
        if not isinstance(values, dict) or set(values) - ORGANIZATION_FIELDS:
            invalid("Unsupported organization fields")
        try:
            validate_payload("v1.0", "organization", values, partial=True)
        except ValueError:
            invalid("Organization fields do not match the Graph schema")
        identifier = uuid5(tenant_id, "organization-profile")
        row = await session.get(SecurityObject, (tenant_id, identifier))
        if row is None:
            session.add(
                SecurityObject(
                    tenant_id=tenant_id,
                    id=identifier,
                    kind="organizationProfile",
                    properties=values,
                )
            )
        else:
            row.properties = values
    if "appRoleAssignments" in body:
        values = body["appRoleAssignments"]
        if not isinstance(values, list) or len(values) > MAX_RECORDS:
            invalid("At most 1000 assignments are supported")
        prepared = []
        seen = set()
        edges = set()
        for value in values:
            if not isinstance(value, dict) or set(value) - {
                "id",
                "principalId",
                "resourceId",
                "appRoleId",
                "principalDisplayName",
                "resourceDisplayName",
                "principalType",
                "createdDateTime",
            }:
                invalid("Invalid app role assignment")
            try:
                validate_payload("v1.0", "appRoleAssignment", value, partial=True)
                identifier, principal_id, resource_id, role = (
                    UUID(value[k]) for k in ("id", "principalId", "resourceId", "appRoleId")
                )
            except (ValueError, KeyError, TypeError):
                invalid("App role assignment IDs must be GUIDs")
            if identifier in seen or (principal_id, resource_id, role) in edges:
                invalid("Duplicate app role assignment")
            seen.add(identifier)
            edges.add((principal_id, resource_id, role))
            existing = await session.get(SecurityObject, (tenant_id, identifier))
            if existing is not None and existing.kind != "principalAppRoleAssignment":
                invalid("Assignment ID is already in use")
            principal = await session.get(User, principal_id)
            kind = "User"
            if principal is None:
                principal = await session.get(Group, principal_id)
                kind = "Group"
            if (
                principal is None
                or principal.tenant_id != tenant_id
                or principal.deleted_at is not None
            ):
                invalid("Assignment principal must be a local active user or group")
            resource = await public_security_object(
                session, tenant_id, "servicePrincipal", str(resource_id)
            )
            if role.int != 0:
                available = resource.get("appRoles", [])
                if not any(
                    x.get("id") == str(role)
                    and x.get("isEnabled") is True
                    and "User" in x.get("allowedMemberTypes", [])
                    for x in available
                ):
                    invalid("Resource does not expose this enabled user app role")
            elif resource.get("appRoles"):
                invalid("Default assignment requires a resource without defined app roles")
            prepared.append(
                (
                    identifier,
                    value
                    | {
                        "id": str(identifier),
                        "principalId": str(principal_id),
                        "resourceId": str(resource_id),
                        "appRoleId": str(role),
                        "principalType": kind,
                        "principalDisplayName": principal.display_name,
                        "resourceDisplayName": resource.get("displayName"),
                        "createdDateTime": value.get("createdDateTime")
                        or datetime.now(UTC).isoformat(),
                    },
                )
            )
        await session.execute(
            delete(SecurityObject).where(
                SecurityObject.tenant_id == tenant_id,
                SecurityObject.kind == "principalAppRoleAssignment",
            )
        )
        for identifier, value in prepared:
            session.add(
                SecurityObject(
                    tenant_id=tenant_id,
                    id=identifier,
                    kind="principalAppRoleAssignment",
                    properties=value,
                )
            )
    if "registeredUsers" in body:
        values = body["registeredUsers"]
        if not isinstance(values, list) or len(values) > MAX_RECORDS:
            invalid("At most 1000 registered user references are supported")
        prepared = set()
        for value in values:
            if not isinstance(value, dict) or set(value) != {"deviceId", "userId"}:
                invalid("Invalid registered user reference")
            try:
                device, user = UUID(value["deviceId"]), UUID(value["userId"])
            except (ValueError, TypeError, AttributeError):
                invalid("Invalid reference IDs")
            await public_security_object(session, tenant_id, "device", str(device))
            target = await session.get(User, user)
            if target is None or target.tenant_id != tenant_id or target.deleted_at is not None:
                invalid("Registered user must be a local active user")
            if (device, user) in prepared:
                invalid("Duplicate registered user reference")
            prepared.add((device, user))
        await session.execute(delete(DeviceUser).where(DeviceUser.tenant_id == tenant_id))
        for device, user in prepared:
            session.add(DeviceUser(tenant_id=tenant_id, device_id=device, user_id=user))
    await session.flush()
    return await collection_state(session, tenant_id)
