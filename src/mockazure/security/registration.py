"""Registration inventory from persisted methods, without invented policy capability."""

from dataclasses import replace

from sqlalchemy import String, case, cast, exists, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by

from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity
from mockazure.authorization.roles import effective_roles, protected_role_rows
from mockazure.identity.models import User
from mockazure.licensing.service import entitlements
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.query.ast import Boolean, Compare, StartsWith
from mockazure.security.models import SecurityObject
from mockazure.security.repository import document

REPORT_ROLES = {
    "Reports Reader",
    "Security Reader",
    "Security Administrator",
    "Global Reader",
    "Global Administrator",
}


async def require_report_read(session, actor):
    try:
        await require_current_identity(actor, session)
    except AuthorizationDenied:
        raise GraphError(403, "Authorization_RequestDenied", "Current identity required") from None
    roles = (
        set(await effective_roles(session, actor.tenant_id, actor.subject_id))
        if actor.token_kind == "delegated"
        else set()
    )
    if "AuditLog.Read.All" not in actor.permissions or (
        actor.token_kind == "delegated" and not roles & REPORT_ROLES
    ):
        raise GraphError(
            403, "Authorization_RequestDenied", "Audit permission and reader role required"
        )
    if (await entitlements(session, actor.tenant_id, actor.subject_id)).tenant_tier == "Free":
        raise GraphError(403, "Authorization_RequestDenied", "Entra ID P1 or P2 required")


def registration_source(tenant):
    method = SecurityObject
    payload = method.properties["payload"]
    kind = payload["@odata.type"].as_string()
    name = case(
        (
            kind == "#microsoft.graph.phoneAuthenticationMethod",
            case(
                (payload["phoneType"].as_string() == "mobile", "mobilePhone"),
                (payload["phoneType"].as_string() == "alternateMobile", "alternateMobilePhone"),
                else_="officePhone",
            ),
        ),
        (kind == "#microsoft.graph.emailAuthenticationMethod", "email"),
        (
            kind == "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod",
            "microsoftAuthenticatorPush",
        ),
        (kind == "#microsoft.graph.fido2AuthenticationMethod", "passKeyDeviceBound"),
    )
    match = (
        method.tenant_id == tenant,
        method.kind == "authenticationMethod",
        method.properties["userId"].as_string() == cast(User.id, String),
    )
    methods = (
        select(func.jsonb_agg(aggregate_order_by(name.distinct(), name)))
        .where(*match)
        .correlate(User)
        .scalar_subquery()
    )
    strong = exists(
        select(method.id).where(
            *match,
            kind.in_(
                [
                    "#microsoft.graph.phoneAuthenticationMethod",
                    "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod",
                    "#microsoft.graph.fido2AuthenticationMethod",
                ]
            ),
        )
    ).correlate(User)
    roles = protected_role_rows(tenant)
    admin = exists(
        select(roles.c.principal_id).where(roles.c.principal_id == User.id)
    ).correlate(User)
    fields = {
        "@odata.type": "#microsoft.graph.userRegistrationDetails",
        "id": cast(User.id, String),
        "userPrincipalName": User.upn,
        "userDisplayName": User.display_name,
        "userType": func.lower(func.coalesce(User.profile["userType"].as_string(), "Member")),
        "isAdmin": admin,
        "isMfaRegistered": strong,
        "methodsRegistered": func.coalesce(methods, literal([], type_=JSONB)),
        "lastUpdatedDateTime": func.now(),
    }
    # These descriptor methods do not enable physical MFA/passwordless or SSPR.
    # Those services are disabled in this profile; CSDL requires boolean values.
    for key in (
        "isMfaCapable",
        "isPasswordlessCapable",
        "isSsprCapable",
        "isSsprEnabled",
        "isSsprRegistered",
        "isSystemPreferredAuthenticationMethodEnabled",
        "userPreferredMethodForSecondaryAuthentication",
    ):
        fields[key] = False
    fields["userPreferredMethodForSecondaryAuthentication"] = "none"
    fields["systemPreferredAuthenticationMethods"] = literal([], type_=JSONB)
    return (
        select(User.id, document(**fields).label("payload"))
        .where(
            User.tenant_id == tenant,
            User.deleted_at.is_(None),
            User.enabled.is_(True),
        )
        .subquery()
    )


def normalize_filter(node):
    if isinstance(node, Boolean):
        return replace(node, children=tuple(normalize_filter(child) for child in node.children))
    if isinstance(node, Compare) and node.left.name in {"userDisplayName", "userPrincipalName"}:
        return (
            replace(node, right=replace(node.right, value=node.right.value.lower()))
            if isinstance(node.right.value, str)
            else node
        )
    if isinstance(node, StartsWith) and node.field.name in {"userDisplayName", "userPrincipalName"}:
        return replace(node, value=replace(node.value, value=node.value.value.lower()))
    return node
