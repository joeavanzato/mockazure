"""Curated administrative metadata; credential/session tables are never exposed."""

from sqlalchemy import String, cast, func

from mockazure.activity.models import Subscription
from mockazure.identity.invitation_models import GuestInvitation
from mockazure.identity.models import Application, DirectoryRole, Grant, ServicePrincipal
from mockazure.mail.collection_models import (
    ExchangeConfiguration,
    ExchangeConfigurationRecord,
    ExchangePermissionRecord,
)
from mockazure.mail.delegation_models import MailboxDelegation
from mockazure.mail.models import MailRule
from mockazure.mail.security_configuration_models import (
    ExchangeAuditSettings,
    ExchangeSecurityRecord,
)
from mockazure.population.adapters.browsing import browser
from mockazure.security.authentication_models import MethodPolicyState
from mockazure.security.models import SecurityObject


def adapters():
    result = []

    def add(key, service, path, row, identifier, fields, *, parent=None, conditions=()):
        # Version hashes use only this explicitly selected public projection.
        from mockazure.population.adapters.arm import json_object

        version = func.md5(cast(json_object(**fields), String))
        result.append(
            browser(
                key,
                service,
                path,
                row,
                identifier,
                version,
                fields,
                parent=parent,
                conditions=conditions,
                limitations=[
                    "Read-only catalog entry. Provision or change through the existing public "
                    "API or documented administrative import.",
                    "Only persisted metadata is shown; service-generated defaults may appear "
                    "only in public API responses.",
                ],
            )
        )

    r = GuestInvitation
    add(
        "identity.guestInvitations",
        "Entra",
        "/v1.0/invitations",
        r,
        r.id,
        {
            "id": r.id,
            "userId": r.user_id,
            "email": r.email,
            "status": r.status,
            "redirectUrl": r.redirect_url,
            "deliveryMode": r.delivery["mode"].as_string(),
            "delivery": r.delivery,
            "createdAt": r.created_at,
        },
    )
    r = Application
    add(
        "directory.applications",
        "Entra",
        "/v1.0/applications",
        r,
        r.object_id,
        {
            "id": r.object_id,
            "appId": r.client_id,
            "displayName": r.display_name,
            "enabled": r.enabled,
            "requiredPermissions": r.required_permissions,
        },
        conditions=(r.deleted_at.is_(None),),
    )
    r = ServicePrincipal
    add(
        "directory.servicePrincipals",
        "Entra",
        "/v1.0/servicePrincipals",
        r,
        r.id,
        {"id": r.id, "appId": r.client_id, "enabled": r.enabled},
        conditions=(r.deleted_at.is_(None),),
    )
    r = Grant
    add(
        "identity.grants",
        "Entra",
        "/v1.0/oauth2PermissionGrants",
        r,
        r.id,
        {
            "id": r.id,
            "clientId": r.client_id,
            "resource": r.resource,
            "kind": r.kind,
            "permissions": r.permissions,
            "userId": r.user_id,
        },
    )
    r = DirectoryRole
    add(
        "identity.directoryRoles",
        "Entra",
        "/v1.0/directoryRoles/{role-id}/members",
        r,
        func.concat(r.user_id, ":", r.role),
        {"userId": r.user_id, "role": r.role},
    )
    r = SecurityObject
    for key, kind, path in (
        ("directory.devices", "device", "/v1.0/devices"),
        (
            "security.conditionalAccessPolicies",
            "conditionalAccessPolicy",
            "/v1.0/identity/conditionalAccess/policies",
        ),
        (
            "security.namedLocations",
            "ipNamedLocation",
            "/v1.0/identity/conditionalAccess/namedLocations",
        ),
        (
            "security.authenticationMethods",
            "authenticationMethod",
            "/v1.0/users/{user-id}/authentication/methods",
        ),
    ):
        fields = {
            "id": r.id,
            "displayName": r.properties["displayName"],
            "type": r.properties["@odata.type"],
        }
        if kind != "authenticationMethod":
            fields.update(
                {
                    name: r.properties[name]
                    for name in (
                        "state",
                        "conditions",
                        "grantControls",
                        "deviceId",
                        "operatingSystem",
                        "ipRanges",
                    )
                }
            )
        add(key, "Entra", path, r, r.id, fields, conditions=(r.kind == kind,))
    r = MethodPolicyState
    add(
        "security.authenticationPolicy",
        "Entra",
        "/v1.0/policies/authenticationMethodsPolicy",
        r,
        r.tenant_id,
        {"id": r.tenant_id, "enforced": r.enforced, "configurations": r.configurations},
    )
    r = MailRule
    add(
        "mail.rules",
        "Mail and Exchange",
        "/v1.0/users/{user-id}/mailFolders/inbox/messageRules",
        r,
        r.id,
        {"id": r.id, "family": r.family, "sequence": r.sequence, "payload": r.payload},
        parent=r.user_id,
    )
    r = MailboxDelegation
    add(
        "mail.delegations",
        "Mail and Exchange",
        "Get-MailboxPermission / Get-RecipientPermission",
        r,
        func.concat(r.principal_id, ":", r.permission, ":", r.deny, ":", r.inheritance),
        {
            "principalId": r.principal_id,
            "permission": r.permission,
            "deny": r.deny,
            "inheritance": r.inheritance,
            "autoMapping": r.auto_mapping,
        },
        parent=r.mailbox_id,
    )
    r = ExchangeConfiguration
    add(
        "exchange.configuration",
        "Mail and Exchange",
        "Get-OrganizationConfig / Get-TransportConfig",
        r,
        r.tenant_id,
        {"organization": r.organization, "transport": r.transport},
    )
    r = ExchangeConfigurationRecord
    add(
        "exchange.configurationRecords",
        "Mail and Exchange",
        "Get-AcceptedDomain / Get-TransportRule",
        r,
        r.id,
        {"id": r.id, "kind": r.kind, "payload": r.payload},
        parent=r.kind,
    )
    r = ExchangePermissionRecord
    add(
        "exchange.permissions",
        "Mail and Exchange",
        "Get-MailboxPermission / Get-RecipientPermission",
        r,
        r.id,
        {"id": r.id, "family": r.family, "payload": r.payload},
        parent=r.mailbox_user_id,
    )
    r = ExchangeSecurityRecord
    add(
        "exchange.security",
        "Mail and Exchange",
        "Exchange security configuration cmdlets",
        r,
        r.id,
        {"id": r.id, "kind": r.kind, "name": r.name_key, "payload": r.payload},
        parent=r.kind,
    )
    r = ExchangeAuditSettings
    add(
        "exchange.auditSettings",
        "Mail and Exchange",
        "Get-AdminAuditLogConfig",
        r,
        r.tenant_id,
        {"id": r.tenant_id, "enabled": r.enabled},
    )
    r = Subscription
    add(
        "activity.subscriptions",
        "Activity feed",
        "/api/v1.0/{tenant}/activity/feed/subscriptions/list",
        r,
        func.concat(r.client_id, ":", r.content_type),
        {
            "clientId": r.client_id,
            "contentType": r.content_type,
            "enabled": r.enabled,
            "startRevision": r.start_revision,
        },
    )
    return result
