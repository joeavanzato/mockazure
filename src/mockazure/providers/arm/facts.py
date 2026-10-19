"""Immutable ARM control-plane facts in the shared evidence transaction."""

import json
from datetime import timedelta
from uuid import UUID, uuid4

from mockazure.evidence.clock import evidence_now
from mockazure.evidence.repository import append_source, lock_state
from mockazure.evidence.risk import actor_snapshot
from mockazure.evidence.schemas import EvidenceSettings, SourceInput, SourceRef
from mockazure.providers.arm.inventory import clean_public
from mockazure.providers.arm.scopes import parse_scope


def validate_arm_facts(facts):
    if not isinstance(facts, dict) or set(facts) != {
        "operation",
        "scope",
        "actor",
        "before",
        "after",
        "correlation_id",
        "provenance",
        "status",
    }:
        raise ValueError("Invalid ARM evidence fact")
    parse_scope(facts["scope"])
    UUID(facts["correlation_id"])
    if (
        not isinstance(facts["operation"], str)
        or not 1 <= len(facts["operation"]) <= 200
        or facts["status"] not in {"Succeeded", "Failed"}
        or facts["provenance"] != "native"
    ):
        raise ValueError("Invalid ARM operation fact")
    if any(not isinstance(facts[name], dict) for name in ("actor", "before", "after")):
        raise ValueError("Invalid ARM state fact")
    clean_public(facts)
    if len(json.dumps(facts, allow_nan=False).encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("ARM evidence exceeds local size limit")
    return facts


async def append_arm_fact(session, actor, operation, scope, before, after, now, correlation_id):
    from mockazure.evidence.sources import enqueue_projection

    if before == after:
        return None
    state = await lock_state(session, actor.tenant_id)
    when = await evidence_now(session, actor.tenant_id, now)
    ref = SourceRef(tenant_id=actor.tenant_id, kind="arm", id=uuid4())
    await append_source(
        session,
        SourceInput(
            ref=ref,
            occurred_at=when,
            available_at=when
            + timedelta(seconds=EvidenceSettings.model_validate(state.settings).delay_seconds),
            facts={
                "operation": operation,
                "scope": scope.display_id,
                "actor": await actor_snapshot(session, actor),
                "before": before,
                "after": after,
                "correlation_id": str(correlation_id),
                "provenance": "native",
                "status": "Succeeded",
            },
        ),
    )
    await enqueue_projection(session, ref)
    # Every committed native ARM fact can feed subscription diagnostics. Some
    # providers also append a CloudEvent with this correlation ID; outbox keys
    # deduplicate that second notification without changing the evidence source.
    from mockazure.cloud.events import lock_cloud_state
    from mockazure.cloud.types import ServiceEvent
    from mockazure.monitor.publication import queue_event

    cloud = await lock_cloud_state(session, actor.tenant_id)
    await queue_event(
        session,
        ServiceEvent(
            correlation_id,
            actor.tenant_id,
            scope.display_id,
            "arm",
            "Administrative",
            operation,
            when,
            {"outcome": "Succeeded"},
        ),
        cloud.generation,
    )
    return ref
