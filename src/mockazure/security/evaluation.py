"""Evaluate persisted policies against current local identity and connection state."""

from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from sqlalchemy import select

from mockazure.directory.models import Group
from mockazure.identity.models import Application, DirectoryRole
from mockazure.licensing.service import entitlements
from mockazure.oauth.errors import OAuthError
from mockazure.providers.graph.relationships import relation_ids
from mockazure.security.catalog import resource_app_id
from mockazure.security.models import SecurityObject
from mockazure.security.policies import normalized_conditions
from mockazure.security.repository import lock_security


@dataclass(frozen=True)
class PolicyDecision:
    blocked: bool
    requires_mfa: bool
    status: str
    results: tuple[dict, ...]


def intersects(values, identifier, groups=()):
    return identifier in values or bool(set(values) & set(groups))


def location_matches(values, peer, locations):
    if "All" in values:
        return True
    if peer is None:
        return False
    for identifier, location in locations.items():
        if identifier not in values and not ("AllTrusted" in values and location.get("isTrusted")):
            continue
        if any(peer in ip_network(item["cidrAddress"]) for item in location["ipRanges"]):
            return True
    return False


async def evaluate_policies(session, tenant, user, resource, context, now) -> PolicyDecision:
    # This lock also covers issuance, so a policy write cannot interleave between
    # this decision and the token/evidence commit.
    await lock_security(session, tenant)
    policies = list(
        await session.scalars(
            select(SecurityObject)
            .where(
                SecurityObject.tenant_id == tenant,
                SecurityObject.kind == "conditionalAccessPolicy",
            )
            .order_by(SecurityObject.id)
        )
    )
    if not policies:
        return PolicyDecision(False, False, "notApplied", ())
    licensed = (await entitlements(session, tenant, user.id)).tenant_tier in {"P1", "P2"}
    parents = relation_ids(tenant, user.id, "transitiveMemberOf").subquery()
    groups = set(
        map(
            str,
            await session.scalars(
                select(Group.id).where(
                    Group.tenant_id == tenant,
                    Group.deleted_at.is_(None),
                    Group.id.in_(select(parents.c.id)),
                )
            ),
        )
    )
    locations = {
        str(row.id): row.properties
        for row in await session.scalars(
            select(SecurityObject).where(
                SecurityObject.tenant_id == tenant,
                SecurityObject.kind == "ipNamedLocation",
            )
        )
    }
    peer = None
    try:
        if context.get("ip_address"):
            peer = ip_address(context["ip_address"])
    except ValueError:
        pass
    resource_id = str(resource_app_id(resource))
    from types import SimpleNamespace

    from mockazure.security.authentication_context import (
        bound_context,
        existing_device,
        valid_method_outcome,
    )
    from mockazure.security.method_policies import enforcement_enabled
    from mockazure.security.roles import role_ids
    from mockazure.workloads.intune import device_observation

    identity = SimpleNamespace(
        id=context.get("session_id"),
        tenant_id=tenant,
        user_id=user.id,
        client_id=context.get("client_id"),
    )
    trusted = await bound_context(session, identity, now) if identity.id else None
    completed = context.get("mfa") == "completed"
    if await enforcement_enabled(session, tenant):
        completed = (
            completed and bool(identity.id) and await valid_method_outcome(session, identity, now)
        )
    device = (
        await existing_device(session, tenant, trusted.device_id)
        if trusted and trusted.device_id
        else None
    )
    platform = trusted.platform if device else None
    if device:
        from mockazure.workloads.repository import scope_row

        # Hold the observation scope through issuance; import/reset cannot change it midway.
        await scope_row(session, tenant, "intune", "deviceManagement", lock="read")
    observation = await device_observation(session, tenant, trusted.device_id) if device else None
    compliant = observation is not None and observation.compliant is True
    roles = {
        role_ids()[name]
        for name in await session.scalars(
            select(DirectoryRole.role).where(
                DirectoryRole.tenant_id == tenant, DirectoryRole.user_id == user.id
            )
        )
        if name in role_ids()
    }
    blocked, requires_mfa, active = False, False, False
    results = []
    for row in policies:
        policy = row.properties
        state = policy["state"]
        conditions = normalized_conditions(policy["conditions"])
        users = conditions["users"]
        applications = conditions["applications"]
        clients = conditions["clientAppTypes"]
        match = (
            (
                "All" in users.get("includeUsers", [])
                or intersects(users.get("includeUsers", []), str(user.id))
                or bool(groups & set(users.get("includeGroups", [])))
                or bool(roles & set(users.get("includeRoles", [])))
            )
            and str(user.id) not in users.get("excludeUsers", [])
            and not groups & set(users.get("excludeGroups", []))
            and not roles & set(users.get("excludeRoles", []))
            and (
                "All" in applications.get("includeApplications", [])
                or resource_id in applications.get("includeApplications", [])
            )
            and resource_id not in applications.get("excludeApplications", [])
            and ("all" in clients or context.get("client_app_type") in clients)
        )
        platforms = conditions.get("platforms")
        if platforms:
            included = platforms.get("includePlatforms", [])
            excluded = platforms.get("excludePlatforms", [])
            match = match and ("all" in included or platform in included)
            match = match and not ("all" in excluded or platform in excluded)
        network = conditions.get("locations")
        unknown_peer = bool(match and network and peer is None)
        if network and not unknown_peer:
            match = match and location_matches(network.get("includeLocations", []), peer, locations)
            match = match and not location_matches(
                network.get("excludeLocations", []), peer, locations
            )
        controls = policy["grantControls"]["builtInControls"]
        report = state == "enabledForReportingButNotEnforced"
        if state == "disabled":
            result = "notEnabled"
        elif not match:
            result = "reportOnlyNotApplied" if report else "notApplied"
        else:
            hard_denial = "block" in controls or unknown_peer or not licensed
            values = {"mfa": completed, "compliantDevice": compliant, "block": False}
            operator = policy["grantControls"]["operator"]
            satisfied = (all if operator == "AND" else any)(values[c] for c in controls)
            can_prompt = (
                "mfa" in controls
                and not completed
                and (operator == "OR" or all(values[c] for c in controls if c != "mfa"))
            )
            denied = hard_denial or not satisfied and not can_prompt
            mfa = not hard_denial and not satisfied and can_prompt
            if report:
                result = (
                    "reportOnlyFailure"
                    if denied
                    else ("reportOnlyInterrupted" if mfa else "reportOnlySuccess")
                )
            else:
                active = True
                blocked |= denied
                requires_mfa |= mfa
                result = "failure" if denied or mfa else "success"
        results.append(
            {
                "id": str(row.id),
                "displayName": policy["displayName"],
                "result": result,
                "enforcedGrantControls": list(controls) if match and state != "disabled" else [],
                "enforcedSessionControls": [],
            }
        )
    failure = blocked or requires_mfa and not completed
    return PolicyDecision(
        blocked,
        requires_mfa,
        "failure" if failure else ("success" if active else "notApplied"),
        tuple(results),
    )


async def client_category(session, identity, flow):
    stored = getattr(identity, "client_app_type", None)
    if stored:
        return stored
    if flow in {"device_code", "device_approval"} or getattr(identity, "device_id", None):
        return "mobileAppsAndDesktopClients"
    app = await session.get(Application, identity.client_id)
    return "mobileAppsAndDesktopClients" if app.client_mode == "public" else "browser"


async def enforce_policies(session, request, identity, user, flow, now, *, allow_mfa_prompt=False):
    peer = request.client.host if request.client else None
    category = await client_category(session, identity, flow)
    context = {
        "ip_address": peer,
        "client_app_type": category,
        "mfa": identity.mfa,
        "session_id": identity.id,
        "client_id": identity.client_id,
    }
    from mockazure.security.authentication_context import (
        inherit_device_binding,
        valid_method_outcome,
    )
    from mockazure.security.method_policies import enforcement_enabled

    await lock_security(session, identity.tenant_id)
    await inherit_device_binding(session, identity, now)
    if (
        identity.mfa == "completed"
        and await enforcement_enabled(session, identity.tenant_id)
        and not await valid_method_outcome(session, identity, now)
    ):
        from mockazure.evidence.authentication import annotate_failure

        raise await annotate_failure(
            OAuthError(
                "interaction_required", "The completed local MFA method is no longer eligible"
            ),
            session,
            request,
            identity.tenant_id,
            identity.client_id,
            user.id,
            flow,
            now,
            error_code=50076,
            resource=identity.resource,
            mfa="not_performed",
        )
    decision = await evaluate_policies(
        session, identity.tenant_id, user, identity.resource, context, now
    )
    request.state.policy_decision = decision
    request.state.policy_client_app_type = category
    if decision.blocked or (decision.requires_mfa and not allow_mfa_prompt):
        from mockazure.evidence.authentication import annotate_failure

        error = OAuthError(
            "access_denied" if decision.blocked else "interaction_required",
            "Conditional Access blocked this request"
            if decision.blocked
            else "Conditional Access requires completion of local MFA",
        )
        raise await annotate_failure(
            error,
            session,
            request,
            identity.tenant_id,
            identity.client_id,
            user.id,
            flow,
            now,
            error_code=53003 if decision.blocked else 50076,
            resource=identity.resource,
            mfa=identity.mfa,
        )
    return decision
