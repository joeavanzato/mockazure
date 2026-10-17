"""Evaluate persisted policies against current local identity and connection state."""

from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from sqlalchemy import select

from mockazure.directory.models import Group
from mockazure.identity.models import Application
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
    completed = context.get("mfa") == "completed"
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
            )
            and str(user.id) not in users.get("excludeUsers", [])
            and not groups & set(users.get("excludeGroups", []))
            and (
                "All" in applications.get("includeApplications", [])
                or resource_id in applications.get("includeApplications", [])
            )
            and resource_id not in applications.get("excludeApplications", [])
            and ("all" in clients or context.get("client_app_type") in clients)
        )
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
            denied = "block" in controls or unknown_peer or not licensed
            mfa = "mfa" in controls
            if report:
                result = (
                    "reportOnlyFailure"
                    if denied
                    else ("reportOnlyInterrupted" if mfa and not completed else "reportOnlySuccess")
                )
            else:
                active = True
                blocked |= denied
                requires_mfa |= mfa
                result = "failure" if denied or mfa and not completed else "success"
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
    context = {"ip_address": peer, "client_app_type": category, "mfa": identity.mfa}
    decision = await evaluate_policies(
        session, identity.tenant_id, user, identity.resource, context, now
    )
    request.state.policy_decision = decision
    request.state.policy_client_app_type = category
    if decision.blocked or (
        decision.requires_mfa and identity.mfa != "completed" and not allow_mfa_prompt
    ):
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
