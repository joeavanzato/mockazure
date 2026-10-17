"""Immutable public security facts in the shared transactional evidence outbox."""

import json
from datetime import timedelta
from uuid import uuid4, uuid5

from mockazure.evidence.clock import evidence_now
from mockazure.evidence.repository import append_source, lock_state
from mockazure.evidence.risk import actor_snapshot
from mockazure.evidence.schemas import (
    EvidenceSettings,
    Projection,
    ProjectionBatch,
    SourceInput,
    SourceRef,
)
from mockazure.evidence.sources import enqueue_projection

PRIVATE_FIELDS = {
    "secretText",
    "secret",
    "verifier",
    "password",
    "privateKey",
    "public_certificate",
    "key",
}


def safe(value):
    if isinstance(value, dict):
        return {key: safe(item) for key, item in value.items() if key not in PRIVATE_FIELDS}
    if isinstance(value, list):
        return [safe(item) for item in value]
    return value


def validate_security_facts(facts):
    if set(facts) != {"audit"} or not isinstance(facts["audit"], dict):
        raise ValueError("Invalid security evidence fact")
    from mockazure.evidence.datasets import validate_public_payload

    validate_public_payload("v1.0", "directoryAudit", facts["audit"])
    if safe(facts) != facts:
        raise ValueError("Private credential field in evidence")
    for target in facts["audit"]["targetResources"]:
        for change in target.get("modifiedProperties", []):
            for field in ("oldValue", "newValue"):
                value = json.loads(change[field])
                if safe(value) != value:
                    raise ValueError("Private credential difference in evidence")
    return facts


async def record_security_audit(session, actor, operation, target, before, after, now):
    state = await lock_state(session, actor.tenant_id)
    when = await evidence_now(session, actor.tenant_id, now)
    source_id = uuid4()
    old, new = safe(before or {}), safe(after or {})
    for values in (old, new):
        for relation in ("owners", "registeredOwners"):
            if relation in values:
                values[relation] = [entry["id"] for entry in values[relation]]
    changed = [
        key
        for key in sorted(old.keys() | new.keys())
        if not key.startswith("@") and old.get(key) != new.get(key)
    ]
    audit = {
        "id": str(uuid5(source_id, "mockazure:security:directoryAudit")),
        "activityDateTime": when.isoformat(),
        "activityDisplayName": operation,
        "category": "Policy"
        if target["type"] in {"conditionalAccessPolicy", "ipNamedLocation"}
        else "RoleManagement"
        if target["type"] == "unifiedRoleAssignment"
        else "ApplicationManagement"
        if target["type"] != "Device"
        else "Device",
        "correlationId": str(source_id),
        "loggedByService": "Core Directory",
        "result": "success",
        "initiatedBy": await actor_snapshot(session, actor),
        "targetResources": [
            target
            | {
                "modifiedProperties": [
                    {
                        "displayName": key,
                        "oldValue": json.dumps(old.get(key)),
                        "newValue": json.dumps(new.get(key)),
                    }
                    for key in changed
                ]
            }
        ],
        "additionalDetails": [],
    }
    ref = SourceRef(tenant_id=actor.tenant_id, kind="security", id=source_id)
    await append_source(
        session,
        SourceInput(
            ref=ref,
            occurred_at=when,
            available_at=when
            + timedelta(seconds=EvidenceSettings.model_validate(state.settings).delay_seconds),
            facts={"audit": audit},
        ),
    )
    await enqueue_projection(session, ref)
    return ref


def project_security(source):
    return ProjectionBatch(
        source=source.ref,
        generation=source.generation,
        receipt_status="projected",
        items=(
            Projection(
                family="directoryAudit",
                id=uuid5(source.ref.id, "mockazure:security:directoryAudit"),
                occurred_at=source.occurred_at,
                available_at=source.available_at,
                payload=source.facts["audit"],
                subject_id=None,
                source_ref=source.ref,
            ),
        ),
    )
