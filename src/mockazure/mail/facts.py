"""Immutable mail operation intent in the shared evidence source transaction."""

import json
from datetime import timedelta
from uuid import uuid4

from mockazure.evidence.clock import evidence_now
from mockazure.evidence.repository import append_source, lock_state
from mockazure.evidence.risk import actor_snapshot
from mockazure.evidence.schemas import EvidenceSettings, SourceInput, SourceRef


def validate_mail_facts(facts):
    if not isinstance(facts, dict) or set(facts) - {"auditIngestionEnabled"} != {
        "operation",
        "actor",
        "mailbox",
        "before",
        "after",
        "provenance",
    }:
        raise ValueError("Invalid mail evidence fact")
    if not isinstance(facts["operation"], str) or len(facts["operation"]) > 80:
        raise ValueError("Invalid mail operation")
    if facts["provenance"] != "native":
        raise ValueError("Invalid native mail provenance")
    for name in ("actor", "mailbox", "before", "after"):
        if not isinstance(facts[name], dict):
            raise ValueError("Invalid mail evidence object")
    if "auditIngestionEnabled" in facts and type(facts["auditIngestionEnabled"]) is not bool:
        raise ValueError("Invalid ingestion eligibility")
    from mockazure.mail.audit_schemas import public_audit_data

    public_audit_data(facts)
    if len(json.dumps(facts, allow_nan=False).encode()) > 2 * 1024 * 1024:
        raise ValueError("Mail evidence fact is too large")
    return facts


async def record_mail_fact(session, actor, operation, mailbox, before, after, now):
    from mockazure.evidence.sources import enqueue_projection
    from mockazure.mail.audit import changed_properties

    if not changed_properties(before, after):
        return None
    from mockazure.mail.security_configuration import audit_ingestion_enabled

    eligible = await audit_ingestion_enabled(session, actor.tenant_id)
    state = await lock_state(session, actor.tenant_id)
    when = await evidence_now(session, actor.tenant_id, now)
    ref = SourceRef(tenant_id=actor.tenant_id, kind="mail", id=uuid4())
    await append_source(
        session,
        SourceInput(
            ref=ref,
            occurred_at=when,
            available_at=when
            + timedelta(seconds=EvidenceSettings.model_validate(state.settings).delay_seconds),
            facts={
                "auditIngestionEnabled": eligible,
                "operation": operation,
                "actor": await actor_snapshot(session, actor),
                "mailbox": mailbox,
                "before": before,
                "after": after,
                "provenance": "native",
            },
        ),
    )
    await enqueue_projection(session, ref)
    return ref
