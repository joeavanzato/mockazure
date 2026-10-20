"""Immutable local site permission facts; no unverified Microsoft audit operation names."""

import json
from datetime import timedelta
from uuid import uuid4

from mockazure.evidence.clock import evidence_now
from mockazure.evidence.repository import append_source, lock_state
from mockazure.evidence.risk import actor_snapshot
from mockazure.evidence.schemas import EvidenceSettings, ProjectionBatch, SourceInput, SourceRef
from mockazure.evidence.sources import enqueue_projection
from mockazure.mail.audit_schemas import public_audit_data

NATIVE_OPERATIONS = (
    {"site.permission.create", "site.permission.delete", "teams.chat.create"}
    | {f"drive.{action}" for action in ("upload", "update", "copy", "delete")}
    | {
        f"teams.{kind}.member.{action}"
        for kind in ("team", "channel", "chat")
        for action in ("add", "update", "delete")
        if (kind, action) != ("chat", "update")
    }
    | {
        f"teams.{kind}.message.{action}"
        for kind in ("channel", "reply", "chat")
        for action in ("send", "update", "softdelete", "undosoftdelete")
    }
)


def validate_workload_facts(value):
    if not isinstance(value, dict) or set(value) != {
        "operation",
        "scope",
        "actor",
        "before",
        "after",
        "provenance",
    }:
        raise ValueError("Invalid workload fact")
    if value["operation"] not in NATIVE_OPERATIONS or value["provenance"] != "native":
        raise ValueError("Unsupported native workload operation")
    if not isinstance(value["scope"], str) or not 1 <= len(value["scope"]) <= 512:
        raise ValueError("Invalid site scope")
    if not all(isinstance(value[key], dict) for key in ("actor", "before", "after")):
        raise ValueError("Invalid workload fact objects")
    public_audit_data(value)
    if len(json.dumps(value, allow_nan=False).encode("utf-8")) > 32768:
        raise ValueError("Workload fact exceeds32KiB")
    return value


async def record_permission_fact(session, actor, operation, scope, before, after, now):
    if before == after:
        return
    state = await lock_state(session, actor.tenant_id)
    when = await evidence_now(session, actor.tenant_id, now)
    ref = SourceRef(tenant_id=actor.tenant_id, kind="workload", id=uuid4())
    await append_source(
        session,
        SourceInput(
            ref=ref,
            occurred_at=when,
            available_at=when
            + timedelta(seconds=EvidenceSettings.model_validate(state.settings).delay_seconds),
            facts={
                "operation": operation,
                "scope": scope,
                "actor": await actor_snapshot(session, actor),
                "before": before,
                "after": after,
                "provenance": "native",
            },
        ),
    )
    await enqueue_projection(session, ref)


def project_workload(source):
    validate_workload_facts(source.facts)
    # Retain/query the native fact; an exact Purview event mapping is not documented
    # for these Graph site-grant operations. Never counterfeit a Microsoft event name.
    return ProjectionBatch(
        source=source.ref, generation=source.generation, items=(), receipt_status="suppressed"
    )
