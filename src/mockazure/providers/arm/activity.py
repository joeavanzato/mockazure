"""Schema-bound Activity Logs with exact 100-nanosecond event identity."""

import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid5

from mockazure.evidence.schemas import Projection, ProjectionBatch
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.inventory import clean_public
from mockazure.providers.arm.scopes import parse_scope

STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,7}))?(Z|[+-]\d{2}:\d{2})")
EPOCH = datetime(1, 1, 1, tzinfo=UTC)


def activity_scope(value):
    return parse_scope(value, allow_subscription_resource=True, allow_provider_registration=True)


def datetime_ticks(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    delta = value.astimezone(UTC) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 10_000_000 + delta.microseconds * 10


def instant(value):
    if not isinstance(value, str) or (match := STAMP.fullmatch(value)) is None:
        raise ValueError("An ISO8601 instant with at most seven fractional digits is required")
    fraction = (match[2] or "").ljust(7, "0")
    zone = "+00:00" if match[3] == "Z" else match[3]
    try:
        floor = datetime.fromisoformat(match[1] + "." + fraction[:6] + zone).astimezone(UTC)
    except OverflowError:
        raise ValueError("Activity timestamp is outside the supported UTC range") from None
    return floor, datetime_ticks(floor) + int(fraction[6])


def event_identity(payload):
    identifier = UUID(payload["eventDataId"])
    match = re.fullmatch(r"(.+)/events/([0-9a-fA-F-]{36})/ticks/([0-9]{1,19})", payload["id"])
    if match is None or UUID(match[2]) != identifier:
        raise ValueError("Activity ARM ID does not contain its eventDataId")
    scope = activity_scope(match[1])
    return identifier, scope, int(match[3])


def validate_activity_payload(payload, *, tenant=None, occurred_at=None, available_at=None):
    validate_arm_payload("EventData", payload, namespace="activity")
    # authorization here is the public typed ARM action/scope object, not an HTTP credential.
    clean_public({key: value for key, value in payload.items() if key != "authorization"})
    clean_public(payload.get("authorization", {}))
    try:
        identifier, scope, id_ticks = event_identity(payload)
        event_at, event_ticks = instant(payload["eventTimestamp"])
        submitted_at, submitted_ticks = instant(payload["submissionTimestamp"])
        if (
            UUID(payload["subscriptionId"]) != scope.subscription_id
            or id_ticks != event_ticks
            or submitted_ticks < event_ticks
        ):
            raise ValueError("Activity identity/time/subscription conflict")
        if "tenantId" in payload:
            recorded_tenant = UUID(payload["tenantId"])
            if tenant is not None and recorded_tenant != tenant:
                raise ValueError("Activity tenant mismatch")
        if "resourceId" in payload and activity_scope(payload["resourceId"]).key != scope.key:
            raise ValueError("Activity resource ID mismatch")
        if (
            "resourceGroupName" in payload
            and payload["resourceGroupName"].lower() != (scope.group or "").lower()
        ):
            raise ValueError("Activity resource group mismatch")
        if auth_scope := payload.get("authorization", {}).get("scope"):
            authority = activity_scope(auth_scope)
            if not authority.contains(scope):
                raise ValueError("Activity authorization scope is unrelated to its resource")
        if occurred_at is not None and occurred_at != event_at:
            raise ValueError("Activity occurrence metadata must match its timestamp")
        if available_at is not None and available_at != submitted_at:
            raise ValueError("Activity availability metadata must match its timestamp")
        if len(payload.get("resourceProviderName", {}).get("value", "")) > 256:
            raise ValueError("Activity provider exceeds the indexed limit")
        if len(payload.get("correlationId", "")) > 128:
            raise ValueError("Activity correlation exceeds the indexed limit")
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Activity requires a complete event identity and timestamps") from error
    return payload


def activity_dimensions(payload):
    _, scope, ticks = event_identity(payload)
    return {
        "arm_subscription_id": scope.subscription_id,
        "arm_scope_key": scope.key,
        "arm_group_name": (scope.group or "").lower(),
        "arm_provider": payload.get("resourceProviderName", {}).get("value", "").lower(),
        "arm_correlation_id": payload.get("correlationId", "").lower(),
        "arm_event_ticks": ticks,
        "arm_available_ticks": instant(payload["submissionTimestamp"])[1],
    }


def project_arm(source):
    from mockazure.providers.arm.facts import validate_arm_facts

    facts = validate_arm_facts(source.facts)
    scope = parse_scope(facts["scope"])
    target = facts["after"].get("id") or facts["before"].get("id") or scope.display_id
    target_scope = activity_scope(target)
    if not scope.contains(target_scope):
        raise ValueError("Native ARM target escaped its captured authorization scope")
    identifier = uuid5(source.ref.id, "mockazure:activityLog")
    ticks = datetime_ticks(source.occurred_at)
    parts = target_scope.resource_parts
    provider_positions = [i for i in range(0, len(parts), 2) if parts[i].lower() == "providers"]
    provider = parts[provider_positions[-1] + 1] if provider_positions else "Microsoft.Resources"
    resource_type = (
        "Microsoft.Resources/providers"
        if len(parts) == 2 and parts[0].lower() == "providers"
        else provider + "/" + "/".join(parts[provider_positions[-1] + 2 :: 2])
        if provider_positions
        else "Microsoft.Resources/resourceGroups"
        if scope.group
        else "Microsoft.Resources/subscriptions"
    )
    payload = {
        "id": f"{target_scope.display_id}/events/{identifier}/ticks/{ticks}",
        "eventDataId": str(identifier),
        "eventTimestamp": source.occurred_at.isoformat(),
        "submissionTimestamp": source.available_at.isoformat(),
        "subscriptionId": str(scope.subscription_id),
        "tenantId": str(source.ref.tenant_id),
        "resourceId": target_scope.display_id,
        "resourceProviderName": {"value": provider},
        "resourceType": {"value": resource_type},
        "authorization": {"action": facts["operation"], "scope": scope.display_id},
        "correlationId": facts["correlation_id"],
        "operationId": facts["correlation_id"],
        "operationName": {"value": facts["operation"]},
        "eventName": {"value": "EndRequest"},
        "category": {"value": "Administrative"},
        "status": {"value": facts["status"]},
        "level": "Error" if facts["status"] == "Failed" else "Informational",
        "properties": {
            "changedProperties": json.dumps(
                sorted(
                    key
                    for key in facts["before"].keys() | facts["after"].keys()
                    if facts["before"].get(key) != facts["after"].get(key)
                )
            )
        },
    }
    if target_scope.group:
        payload["resourceGroupName"] = target_scope.group
    actor = facts["actor"]
    caller = actor.get("user", {}).get("userPrincipalName") or actor.get("app", {}).get("appId")
    if caller:
        payload["caller"] = caller
    validate_activity_payload(
        payload,
        tenant=source.ref.tenant_id,
        occurred_at=source.occurred_at,
        available_at=source.available_at,
    )
    return ProjectionBatch(
        source=source.ref,
        generation=source.generation,
        receipt_status="projected",
        items=(
            Projection(
                family="activityLog",
                id=identifier,
                occurred_at=source.occurred_at,
                available_at=source.available_at,
                payload=payload,
                subject_id=None,
                source_ref=source.ref,
            ),
        ),
    )
