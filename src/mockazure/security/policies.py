"""Declared v1.0 Conditional Access contracts and tenant-local policy state."""

import copy
import ipaddress
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.directory.models import Group
from mockazure.identity.models import Application, User
from mockazure.licensing.service import entitlements
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.audit import record_security_audit
from mockazure.security.catalog import resource_app_id
from mockazure.security.models import SecurityObject
from mockazure.security.permissions import forbidden
from mockazure.security.repository import missing

WRITE_ROLES = frozenset(
    {"Security Administrator", "Conditional Access Administrator", "Global Administrator"}
)
READ_ROLES = WRITE_ROLES | {
    "Security Reader",
    "Global Reader",
    "Global Secure Access Administrator",
}
POLICY_FIELDS = {
    "displayName",
    "description",
    "state",
    "conditions",
    "grantControls",
    "sessionControls",
}
LOCATION_FIELDS = {"@odata.type", "displayName", "isTrusted", "ipRanges"}


def invalid(message="Policy is outside the supported Conditional Access profile"):
    raise GraphError(400, "Request_BadRequest", message)


def shaped(value, kind):
    try:
        validate_payload("v1.0", kind, value, partial=True)
    except (ValueError, TypeError):
        invalid("Request does not match the Graph v1.0 schema")


def declared(value, allowed):
    if not isinstance(value, dict) or set(value) - allowed:
        invalid()


def selectors(values, special=()):
    if (
        not isinstance(values, list)
        or len(values) > 1000
        or any(not isinstance(v, str) for v in values)
    ):
        invalid("Invalid target selectors")
    normalized = []
    for value in values:
        if value not in special:
            try:
                value = str(UUID(value))
            except ValueError:
                invalid("Invalid target identifier")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        invalid("Duplicate target selectors")
    if set(values) & {"All", "None"} and len(values) != 1:
        invalid("All/None must be the sole selector")
    return normalized


def normalized_conditions(conditions):
    """Canonicalize persisted selectors too, including pre-normalization policies."""
    result = copy.deepcopy(conditions)
    for category, fields in (
        ("users", ("includeUsers", "excludeUsers", "includeGroups", "excludeGroups")),
        ("applications", ("includeApplications", "excludeApplications")),
        ("locations", ("includeLocations", "excludeLocations")),
    ):
        target = result.get(category) or {}
        for field in fields:
            if field in target:
                target[field] = [
                    value if value in {"All", "None", "AllTrusted"} else str(UUID(value))
                    for value in target[field]
                ]
    return result


def only_empty(value, allowed):
    for key in allowed:
        if key in value and value[key] not in (None, []):
            invalid("This condition/control is not enforced by the declared profile")


def validate_policy(body: dict, *, partial=False) -> dict:
    declared(body, POLICY_FIELDS)
    shaped(body, "conditionalAccessPolicy")
    body = copy.deepcopy(body)
    if not partial and not {"displayName", "state", "conditions", "grantControls"} <= body.keys():
        invalid("Policy displayName, state, conditions and grantControls are required")
    if "displayName" in body and not body["displayName"].strip():
        invalid("Policy displayName must not be empty")
    if body.get("sessionControls") is not None:
        invalid("Session controls are unsupported")
    if "conditions" in body:
        conditions = body["conditions"]
        empty_fields = {
            "userRiskLevels",
            "signInRiskLevels",
            "servicePrincipalRiskLevels",
            "platforms",
            "devices",
            "clientApplications",
            "authenticationFlows",
            "insiderRiskLevels",
        }
        declared(
            conditions, {"users", "applications", "clientAppTypes", "locations"} | empty_fields
        )
        only_empty(conditions, empty_fields)
        if not partial and not {"users", "applications", "clientAppTypes"} <= conditions.keys():
            invalid("User, resource application and client category targets are required")
        if "clientAppTypes" in conditions:
            clients = conditions["clientAppTypes"]
            if not clients or not set(clients) <= {"all", "browser", "mobileAppsAndDesktopClients"}:
                invalid("Unsupported client application category")
            if len(set(clients)) != len(clients) or "all" in clients and len(clients) != 1:
                invalid("Invalid client application combination")
        for key, fields, empties, specials in (
            (
                "users",
                {"includeUsers", "excludeUsers", "includeGroups", "excludeGroups"},
                {
                    "includeRoles",
                    "excludeRoles",
                    "includeGuestsOrExternalUsers",
                    "excludeGuestsOrExternalUsers",
                },
                {"includeUsers": {"All", "None"}},
            ),
            (
                "applications",
                {"includeApplications", "excludeApplications"},
                {
                    "includeUserActions",
                    "applicationFilter",
                    "includeAuthenticationContextClassReferences",
                },
                {"includeApplications": {"All"}},
            ),
            (
                "locations",
                {"includeLocations", "excludeLocations"},
                set(),
                {"includeLocations": {"All", "AllTrusted"}, "excludeLocations": {"AllTrusted"}},
            ),
        ):
            if key not in conditions:
                continue
            value = conditions[key]
            if key == "locations" and value is None:
                continue
            declared(value, fields | empties)
            only_empty(value, empties)
            for field in fields & value.keys():
                value[field] = selectors(value[field], specials.get(field, ()))
            if not partial:
                includes = [
                    v
                    for field, values in value.items()
                    if field.startswith("include") and field in fields
                    for v in values
                    if v != "None"
                ]
                if not includes:
                    invalid("At least one included target is required")
    if "grantControls" in body:
        controls = body["grantControls"]
        declared(
            controls,
            {
                "operator",
                "builtInControls",
                "customAuthenticationFactors",
                "termsOfUse",
                "authenticationStrength",
            },
        )
        only_empty(
            controls, {"customAuthenticationFactors", "termsOfUse", "authenticationStrength"}
        )
        if not partial and not {"operator", "builtInControls"} <= controls.keys():
            invalid("Grant operator and built-in controls are required")
        if "operator" in controls and controls["operator"] not in {"AND", "OR"}:
            invalid("Unsupported grant operator")
        if "builtInControls" in controls:
            values = controls["builtInControls"]
            if not values or not set(values) <= {"block", "mfa"} or len(set(values)) != len(values):
                invalid("Only block or mfa grant controls are supported")
            if "block" in values and len(values) != 1:
                invalid("Block cannot be combined with another grant control")
    return copy.deepcopy(body)


def validate_named_location(body: dict, *, partial=False) -> dict:
    declared(body, LOCATION_FIELDS)
    shaped(body, "ipNamedLocation")
    if "@odata.type" in body and body["@odata.type"] != "#microsoft.graph.ipNamedLocation":
        invalid("Only IP named locations are supported")
    if not partial and not {"@odata.type", "displayName", "ipRanges"} <= body.keys():
        invalid("IP location type, displayName and ranges are required")
    if "displayName" in body and not body["displayName"].strip():
        invalid("Location displayName must not be empty")
    value = copy.deepcopy(body)
    if "ipRanges" in value:
        ranges = value["ipRanges"]
        if not ranges or len(ranges) > 2000:
            invalid("An IP location requires 1–2000 ranges")
        for item in ranges:
            cidr = item.get("cidrAddress")
            if not isinstance(cidr, str) or "/" not in cidr:
                invalid("An explicit CIDR prefix is required")
            try:
                network = ipaddress.ip_network(cidr, strict=False)
            except ValueError:
                invalid("Invalid CIDR address")
            if item.get("@odata.type") != f"#microsoft.graph.iPv{network.version}CidrRange":
                invalid("CIDR address family does not match its type")
            item["cidrAddress"] = str(network)
    return value


def merge_policy(before, changes):
    result = copy.deepcopy(before)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_policy(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


async def require_policy_access(session, actor, kind, operation):
    write = operation != "GET"
    required = {"Policy.ReadWrite.ConditionalAccess"} if write else {"Policy.Read.All"}
    await permitted(
        actor,
        OperationRequirement(
            "graph",
            frozenset(required),
            frozenset(required),
            WRITE_ROLES if write else READ_ROLES,
        ),
        session,
    )
    if write:
        readers = {"Policy.Read.All"}
        if kind == "conditionalAccessPolicy" and operation in {"POST", "PATCH"}:
            readers.add("Application.Read.All")
        if not actor.permissions & readers:
            forbidden()
    entitlement = await entitlements(
        session, actor.tenant_id, actor.subject_id if actor.token_kind == "delegated" else None
    )
    if entitlement.tenant_tier not in {"P1", "P2"}:
        raise GraphError(
            403,
            "Authorization_RequestDenied",
            "Conditional Access requires an enabled P1 or P2 service plan",
        )


async def validate_references(session, tenant, body):
    conditions = body["conditions"]
    for key, fields, model in (
        ("users", ("includeUsers", "excludeUsers"), User),
        ("users", ("includeGroups", "excludeGroups"), Group),
        ("applications", ("includeApplications", "excludeApplications"), Application),
        ("locations", ("includeLocations", "excludeLocations"), SecurityObject),
    ):
        target = conditions.get(key) or {}
        for field in fields:
            for value in target.get(field, []):
                if value in {"All", "None", "AllTrusted"}:
                    continue
                identifier = UUID(value)
                if model is Application and identifier in {
                    resource_app_id(r) for r in ("graph", "exchange", "arm")
                }:
                    continue
                column = model.client_id if model is Application else model.id
                query = select(column).where(model.tenant_id == tenant, column == identifier)
                if model is SecurityObject:
                    query = query.where(SecurityObject.kind == "ipNamedLocation")
                else:
                    query = query.where(model.deleted_at.is_(None))
                if await session.scalar(query) is None:
                    invalid("Policy reference is not an available tenant object")


async def mutate_policy(session, actor, kind, operation, identifier, body):
    row = None
    before = None
    if operation != "POST":
        try:
            row = await session.get(SecurityObject, (actor.tenant_id, UUID(str(identifier))))
        except ValueError:
            missing()
        if row is None or row.kind != kind:
            missing()
        before = dict(row.properties)
    if operation == "DELETE":
        if kind == "ipNamedLocation":
            existing = await session.scalars(
                select(SecurityObject.properties).where(
                    SecurityObject.tenant_id == actor.tenant_id,
                    SecurityObject.kind == "conditionalAccessPolicy",
                )
            )
            used = any(
                str(row.id) in targets.get(field, [])
                for policy in existing
                for targets in [normalized_conditions(policy["conditions"]).get("locations") or {}]
                for field in ("includeLocations", "excludeLocations")
            )
            if used:
                raise GraphError(
                    409, "Request_Conflict", "Named location is referenced by a policy"
                )
        await session.delete(row)
        after = None
    else:
        validator = (
            validate_policy if kind == "conditionalAccessPolicy" else validate_named_location
        )
        changes = validator(body, partial=operation == "PATCH")
        editable = POLICY_FIELDS if kind == "conditionalAccessPolicy" else LOCATION_FIELDS
        value = merge_policy({k: v for k, v in (before or {}).items() if k in editable}, changes)
        value = validator(value)
        if kind == "conditionalAccessPolicy":
            await validate_references(session, actor.tenant_id, value)
        now = datetime.now(UTC).isoformat()
        if row is None:
            row = SecurityObject(tenant_id=actor.tenant_id, id=uuid4(), kind=kind, properties={})
            session.add(row)
        value.update(
            {
                "id": str(row.id),
                "@odata.type": "#microsoft.graph." + kind,
                "createdDateTime": before["createdDateTime"] if before else now,
                "modifiedDateTime": now if before else None,
            }
        )
        if kind == "ipNamedLocation":
            value.setdefault("isTrusted", False)
        row.properties = value
        after = value
    await session.flush()
    await record_security_audit(
        session,
        actor,
        {"POST": "Add", "PATCH": "Update", "DELETE": "Delete"}[operation] + " " + kind,
        {"id": str(row.id), "type": kind, "displayName": (after or before)["displayName"]},
        before,
        after,
        datetime.now(UTC),
    )
    return after
