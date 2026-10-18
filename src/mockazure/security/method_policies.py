"""Bounded v1.0 method policies over canonical local enrollments."""

import copy
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.directory.models import Group
from mockazure.identity.models import User
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.providers.graph.relationships import relation_ids
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.audit import record_security_audit
from mockazure.security.authentication_models import MethodPolicyState
from mockazure.security.methods import PHONE_IDS, stored_methods
from mockazure.security.repository import lock_security

KINDS = {
    "Sms": "smsAuthenticationMethodConfiguration",
    "MicrosoftAuthenticator": "microsoftAuthenticatorAuthenticationMethodConfiguration",
}
TARGETS = {
    "Sms": "smsAuthenticationMethodTarget",
    "MicrosoftAuthenticator": "microsoftAuthenticatorAuthenticationMethodTarget",
}
ROLES = frozenset({"Authentication Policy Administrator", "Global Administrator"})


def identifier(value):
    for name in KINDS:
        if value.lower() == name.lower():
            return name
    raise GraphError(501, "NotSupported", "This authentication method policy is unsupported")


def default_configuration(name):
    target = {"id": "all_users", "targetType": "group", "isRegistrationRequired": False}
    target.update({"isUsableForSignIn": False} if name == "Sms" else {"authenticationMode": "push"})
    return {
        "@odata.type": "#microsoft.graph." + KINDS[name],
        "id": name,
        "state": "enabled",
        "includeTargets": [target],
        "excludeTargets": [],
    }


async def policy_state(session, tenant):
    return await session.get(MethodPolicyState, tenant, populate_existing=True)


async def enforcement_enabled(session, tenant):
    row = await policy_state(session, tenant)
    return row is None or row.enforced


async def configurations(session, tenant):
    row = await policy_state(session, tenant)
    return [
        copy.deepcopy((row.configurations if row else {}).get(name, default_configuration(name)))
        for name in KINDS
    ]


async def policy_document(session, tenant):
    row = await policy_state(session, tenant)
    return {
        "id": "authenticationMethodsPolicy",
        "displayName": "Authentication Methods Policy",
        "description": "Tenant authentication method policies",
        "policyVersion": "1.5",
        "lastModifiedDateTime": row.modified_at.isoformat() if row and row.modified_at else None,
        "authenticationMethodConfigurations": await configurations(session, tenant),
    }


def invalid(message="Configuration is outside the supported authentication method policy profile"):
    raise GraphError(400, "Request_BadRequest", message)


def validate_configuration(name, body):
    kind = KINDS[name]
    if not isinstance(body, dict) or set(body) - {
        "@odata.type",
        "id",
        "state",
        "includeTargets",
        "excludeTargets",
    }:
        invalid()
    if body.get("@odata.type") != "#microsoft.graph." + kind:
        invalid("The matching @odata.type is required")
    if "id" in body and body["id"] != name:
        invalid("The configuration ID cannot change")
    try:
        validate_payload(
            "v1.0", kind, {k: v for k, v in body.items() if k != "includeTargets"}, partial=True
        )
    except (ValueError, TypeError):
        invalid("Invalid configuration shape")
    if "state" in body and body["state"] not in {"enabled", "disabled"}:
        invalid()
    result = copy.deepcopy(body)
    for field in ("includeTargets", "excludeTargets"):
        if field not in result:
            continue
        values = result[field]
        if not isinstance(values, list) or len(values) > 1000:
            invalid("Targets must be a bounded collection")
        seen = set()
        for target in values:
            target_kind = TARGETS[name] if field == "includeTargets" else "excludeTarget"
            try:
                validate_payload("v1.0", target_kind, target, partial=True)
            except (ValueError, TypeError):
                invalid("Invalid target shape")
            if target.get("targetType") != "group" or not isinstance(target.get("id"), str):
                invalid("Only explicit group targets are supported")
            key = target["id"]
            if key != "all_users" or field == "excludeTargets":
                try:
                    key = str(UUID(key))
                except ValueError:
                    invalid("Invalid group identifier")
            if key in seen:
                invalid("Duplicate group target")
            seen.add(key)
            target["id"] = key
            if target.get("isRegistrationRequired", False) is not False:
                invalid("Registration enforcement is unsupported")
            if name == "Sms" and target.get("isUsableForSignIn", False) is not False:
                invalid("Primary SMS sign-in is unsupported")
            if name == "MicrosoftAuthenticator" and target.get(
                "authenticationMode", "push"
            ) not in {"push", "any"}:
                invalid("Passwordless authentication is unsupported")
        if "all_users" in seen and len(seen) > 1:
            invalid("all_users must be the sole included target")
    try:
        validate_payload(
            "v1.0", kind, {k: v for k, v in result.items() if k != "includeTargets"}, partial=True
        )
    except (ValueError, TypeError):
        invalid("Invalid configuration shape")
    return result


async def require_policy_access(session, actor, *, write=False, root=False):
    names = {"Policy.ReadWrite.AuthenticationMethod"}
    if not write:
        names.add("Policy.Read.AuthenticationMethod")
        if root:
            names.add("Policy.Read.All")
    await permitted(
        actor,
        OperationRequirement(
            "graph",
            frozenset(names),
            frozenset(names),
            ROLES if write else ROLES | {"Global Reader"},
        ),
        session,
    )


async def update_configuration(session, actor, name, body):
    await lock_security(session, actor.tenant_id)
    await require_policy_access(session, actor, write=True)
    changes = validate_configuration(name, body)
    for field in ("includeTargets", "excludeTargets"):
        for target in changes.get(field, []):
            if target["id"] == "all_users":
                continue
            group = await session.get(Group, UUID(target["id"]), populate_existing=True)
            if group is None or group.tenant_id != actor.tenant_id or group.deleted_at:
                invalid("A current group in this tenant is required")
    before = next(v for v in await configurations(session, actor.tenant_id) if v["id"] == name)
    after = before | changes
    row = await policy_state(session, actor.tenant_id)
    if row is None:
        row = MethodPolicyState(tenant_id=actor.tenant_id, enforced=True, configurations={})
        session.add(row)
    row.configurations = row.configurations | {name: after}
    row.enforced, row.modified_at = True, datetime.now(UTC)
    await session.flush()
    await record_security_audit(
        session,
        actor,
        "Update authentication method policy",
        {"id": name, "type": KINDS[name], "displayName": name},
        before,
        after,
        row.modified_at,
    )


async def eligible_mfa_methods(session, tenant_id: UUID, user_id: UUID) -> tuple[str, ...]:
    user = await session.get(User, user_id, populate_existing=True)
    if user is None or user.tenant_id != tenant_id or not user.enabled or user.deleted_at:
        return ()
    parents = relation_ids(tenant_id, user_id, "transitiveMemberOf").subquery()
    groups = set(
        map(
            str,
            await session.scalars(
                select(Group.id).where(
                    Group.tenant_id == tenant_id,
                    Group.deleted_at.is_(None),
                    Group.id.in_(select(parents.c.id)),
                )
            ),
        )
    )
    allowed = set()
    for config in await configurations(session, tenant_id):
        included = {t["id"] for t in config["includeTargets"]}
        excluded = {t["id"] for t in config["excludeTargets"]}
        if (
            config["state"] == "enabled"
            and ("all_users" in included or included & groups)
            and not excluded & groups
        ):
            allowed.add(config["id"])
    methods = await stored_methods(session, tenant_id, user)
    return tuple(
        sorted(
            v["id"]
            for v in methods
            if (
                "Sms" in allowed
                and v["@odata.type"] == "#microsoft.graph.phoneAuthenticationMethod"
                and v["id"] == PHONE_IDS["mobile"]
            )
            or (
                "MicrosoftAuthenticator" in allowed
                and v["@odata.type"]
                == "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod"
            )
        )
    )
