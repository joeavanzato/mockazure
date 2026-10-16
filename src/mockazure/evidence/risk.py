"""Bounded risk observations and versioned operator decisions; no proprietary scoring."""

import json
from datetime import datetime, timedelta
from uuid import UUID, uuid4, uuid5

from pydantic import Field
from sqlalchemy import select

from mockazure.admin.errors import AdminError
from mockazure.evidence.models import (
    EvidenceObservation,
    EvidenceSource,
    EvidenceState,
    EvidenceVersion,
    RiskActionCutoff,
)
from mockazure.evidence.schemas import (
    EvidenceSettings,
    Projection,
    ProjectionBatch,
    RiskObservation,
    RiskObservationResult,
    SourceInput,
    SourceRef,
    StrictModel,
)
from mockazure.identity.models import Application, ServicePrincipal, User
from mockazure.providers.graph.schema_reference import validate_payload

UNRESOLVED = {"atRisk", "confirmedCompromised"}
LEVEL = {"none": 0, "low": 1, "medium": 2, "high": 3}


class RiskFacts(StrictModel):
    input: RiskObservation
    user_display_name: str = Field(max_length=200)
    user_principal_name: str = Field(max_length=320)


class RiskActionFacts(StrictModel):
    action: str
    user_ids: tuple[UUID, ...]
    audit: dict


def validate_risk_facts(kind, facts):
    if kind == "risk":
        return RiskFacts.model_validate(facts)
    value = RiskActionFacts.model_validate(facts)
    if value.action not in {"dismiss", "confirmCompromised"} or not 1 <= len(value.user_ids) <= 100:
        raise ValueError("Unsupported risk action")
    validate_payload("v1.0", "directoryAudit", value.audit)
    return value


async def record_risk_observation(session, tenant_id, value):
    from mockazure.evidence.repository import append_source, lock_state
    from mockazure.evidence.sources import enqueue_projection

    value = RiskObservation.model_validate(value.model_dump())
    state = await lock_state(session, tenant_id)
    existing = await session.get(EvidenceSource, (tenant_id, "risk", value.request_id))
    if existing:
        if existing.facts["input"] != value.model_dump(mode="json"):
            raise AdminError(
                409, "EvidenceConflict", "Request ID has different risk observation content"
            )
        return RiskObservationResult(
            detection_id=uuid5(value.request_id, "mockazure:m3b:riskDetection:0"),
            user_id=value.user_id,
            revision=existing.source_revision,
        )
    person = await session.scalar(
        select(User).where(
            User.tenant_id == tenant_id,
            User.id == value.user_id,
            User.enabled.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if person is None:
        raise AdminError(404, "IdentityNotFound", "An active tenant-local user is required")
    if value.sign_in_id:
        linked = await session.get(EvidenceObservation, (tenant_id, "signIn", value.sign_in_id))
        if linked is None or linked.subject_id != value.user_id:
            raise AdminError(404, "EvidenceNotFound", "Sign-in does not belong to this user")
    facts = RiskFacts(
        input=value, user_display_name=person.display_name, user_principal_name=person.upn
    )
    settings = EvidenceSettings.model_validate(state.settings)
    ref = SourceRef(tenant_id=tenant_id, kind="risk", id=value.request_id)
    await append_source(
        session,
        SourceInput(
            ref=ref,
            occurred_at=value.occurred_at,
            available_at=value.occurred_at + timedelta(seconds=settings.delay_seconds),
            facts=facts.model_dump(mode="json"),
        ),
    )
    await enqueue_projection(session, ref)
    return RiskObservationResult(
        detection_id=uuid5(value.request_id, "mockazure:m3b:riskDetection:0"),
        user_id=value.user_id,
        revision=state.source_revision,
    )


def project_risk(source):
    facts = validate_risk_facts(source.ref.kind, source.facts)
    if source.ref.kind == "risk_action":
        items = [
            Projection(
                family="directoryAudit",
                id=UUID(facts.audit["id"]),
                payload=facts.audit,
                occurred_at=source.occurred_at,
                available_at=source.available_at,
                subject_id=None,
                source_ref=source.ref,
            )
        ]
    else:
        value = facts.input
        common = {
            "userDisplayName": facts.user_display_name,
            "userPrincipalName": facts.user_principal_name,
            "riskLevel": value.level,
            "riskState": "atRisk",
            "riskDetail": "none",
        }
        stamp = value.occurred_at.isoformat()
        detection = {
            **common,
            "id": str(uuid5(source.ref.id, "mockazure:m3b:riskDetection:0")),
            "userId": str(value.user_id),
            "riskEventType": value.event_type,
            "activity": "signin" if value.sign_in_id else "user",
            "activityDateTime": stamp,
            "detectedDateTime": stamp,
            "lastUpdatedDateTime": stamp,
            "detectionTimingType": "offline",
            "source": "MockAzure",
            "ipAddress": None,
            "location": None,
            "requestId": str(value.request_id),
            "correlationId": None,
            "additionalInfo": None,
            "tokenIssuerType": "AzureAD",
        }
        risky = {
            **common,
            "id": str(value.user_id),
            "isDeleted": False,
            "isProcessing": False,
            "riskLastUpdatedDateTime": stamp,
        }
        items = [
            Projection(
                family=family,
                id=UUID(payload["id"]),
                payload=payload,
                occurred_at=source.occurred_at,
                available_at=source.available_at,
                subject_id=value.user_id,
                source_ref=source.ref,
            )
            for family, payload in (("riskDetection", detection), ("riskyUser", risky))
        ]
    return ProjectionBatch(
        source=source.ref,
        generation=source.generation,
        items=tuple(items),
        receipt_status="projected",
    )


def at_or_before(when, revision, historical, cutoff):
    stamp = datetime.fromisoformat(cutoff["occurred_at"])
    return when < stamp or (when == stamp and (historical or revision <= cutoff["source_revision"]))


def apply_risk_cutoff(payload, cutoff, *, occurred_at=None, source_revision=0, historical=False):
    result = dict(payload)
    if cutoff is None:
        return result
    family = (
        "signIn"
        if "createdDateTime" in result
        else "riskDetection"
        if "riskEventType" in result
        else "riskyUser"
    )
    field = {
        "signIn": "createdDateTime",
        "riskDetection": "activityDateTime",
        "riskyUser": "riskLastUpdatedDateTime",
    }[family]
    stamp = occurred_at or datetime.fromisoformat(result[field].replace("Z", "+00:00"))
    dismissed = cutoff.payload.get("dismiss")
    if (
        dismissed
        and at_or_before(stamp, source_revision, historical, dismissed)
        and result.get("riskState") in UNRESOLVED
    ):
        result.update(riskState="dismissed", riskDetail="adminDismissedAllRiskForUser")
        result["riskLevelAggregated" if family == "signIn" else "riskLevel"] = "none"
        if family == "riskDetection":
            result["lastUpdatedDateTime"] = dismissed["occurred_at"]
    latest = {
        "occurred_at": cutoff.occurred_at.isoformat(),
        "source_revision": cutoff.source_revision,
    }
    if family == "riskyUser" and at_or_before(stamp, source_revision, historical, latest):
        if cutoff.action == "confirmCompromised":
            result.update(
                riskLevel="high",
                riskState="confirmedCompromised",
                riskDetail="adminConfirmedUserCompromised",
            )
        else:
            result.update(
                riskLevel="none", riskState="dismissed", riskDetail="adminDismissedAllRiskForUser"
            )
        result["riskLastUpdatedDateTime"] = cutoff.occurred_at.isoformat()
    return result


async def current_version(session, tenant, family, identifier):
    return await session.scalar(
        select(EvidenceVersion)
        .join(EvidenceState, EvidenceState.tenant_id == EvidenceVersion.tenant_id)
        .where(
            EvidenceVersion.generation == EvidenceState.generation,
            EvidenceVersion.tenant_id == tenant,
            EvidenceVersion.family == family,
            EvidenceVersion.record_id == identifier,
            EvidenceVersion.valid_to.is_(None),
        )
    )


async def version_payload(session, row, payload, revision):
    if payload == row.payload:
        return False
    checked = dict(payload)
    if row.family == "signIn":
        checked.setdefault("servicePrincipalId", "")
    validate_payload("beta", row.family, checked)
    row.valid_to = revision
    await session.flush()
    session.add(
        EvidenceVersion(
            tenant_id=row.tenant_id,
            generation=row.generation,
            source_kind=row.source_kind,
            source_id=row.source_id,
            family=row.family,
            record_id=row.record_id,
            valid_from=revision,
            event_time=datetime.fromisoformat(payload["riskLastUpdatedDateTime"])
            if row.family == "riskyUser"
            else row.event_time,
            available_at=row.available_at,
            subject_id=row.subject_id,
            app_id=row.app_id,
            event_type=row.event_type,
            payload=payload,
        )
    )
    return True


async def projection_payload(session, source, item):
    cutoff = (
        await session.get(RiskActionCutoff, (source.ref.tenant_id, item.subject_id))
        if item.subject_id
        else None
    )
    if cutoff is not None and cutoff.generation != source.generation:
        cutoff = None
    value = (
        apply_risk_cutoff(
            item.payload,
            cutoff,
            occurred_at=item.occurred_at,
            source_revision=source.source_revision,
            historical=source.ref.kind == "historical",
        )
        if item.family != "directoryAudit"
        else item.payload
    )
    if source.ref.kind == "risk" and item.family == "riskyUser":
        existing = await current_version(session, source.ref.tenant_id, item.family, item.id)
        if existing is not None:
            previous = existing.payload
            # The declared aggregate is the strongest unresolved observation, not a risk score.
            if previous.get("riskState") in UNRESOLVED:
                if value.get("riskState") not in UNRESOLVED or LEVEL.get(
                    previous.get("riskLevel"), 0
                ) > LEVEL.get(value.get("riskLevel"), 0):
                    value = {
                        **value,
                        **{k: previous[k] for k in ("riskLevel", "riskState", "riskDetail")},
                    }
                if previous.get("riskState") == "confirmedCompromised":
                    value.update(
                        riskState="confirmedCompromised", riskDetail="adminConfirmedUserCompromised"
                    )
            if datetime.fromisoformat(previous["riskLastUpdatedDateTime"]) > datetime.fromisoformat(
                value["riskLastUpdatedDateTime"]
            ):
                value["riskLastUpdatedDateTime"] = previous["riskLastUpdatedDateTime"]
    return value


async def publish_linked_risk(session, source, revision):
    if source.ref.kind != "risk":
        return
    facts = RiskFacts.model_validate(source.facts)
    value = facts.input
    if not value.sign_in_id:
        return
    row = await current_version(session, source.ref.tenant_id, "signIn", value.sign_in_id)
    if row is None:
        return
    payload = dict(row.payload)
    payload.update(
        riskState="atRisk",
        riskDetail="none",
        riskLevelAggregated=max(
            (payload.get("riskLevelAggregated", "none"), value.level), key=lambda x: LEVEL.get(x, 0)
        ),
        riskEventTypes_v2=list(
            dict.fromkeys([*(payload.get("riskEventTypes_v2") or []), value.event_type])
        ),
    )
    cutoff = await session.get(RiskActionCutoff, (source.ref.tenant_id, value.user_id))
    if cutoff and cutoff.generation != source.generation:
        cutoff = None
    payload = apply_risk_cutoff(
        payload, cutoff, occurred_at=source.occurred_at, source_revision=source.source_revision
    )
    await version_payload(session, row, payload, revision)


async def actor_snapshot(session, actor):
    if actor.token_kind == "delegated":
        user = await session.get(User, actor.subject_id)
        return {
            "user": {
                "id": str(user.id),
                "displayName": user.display_name,
                "userPrincipalName": user.upn,
            }
        }
    app = await session.get(Application, actor.client_id)
    sp = await session.scalar(
        select(ServicePrincipal).where(
            ServicePrincipal.tenant_id == actor.tenant_id,
            ServicePrincipal.client_id == actor.client_id,
        )
    )
    return {
        "app": {
            "appId": str(actor.client_id),
            "displayName": app.display_name,
            "servicePrincipalId": str(sp.id),
            "servicePrincipalName": app.display_name,
        }
    }


async def apply_risk_action(session, actor, action, user_ids, now, correlation_id):
    from mockazure.evidence.clock import evidence_now
    from mockazure.evidence.repository import append_source, lock_state
    from mockazure.evidence.sources import enqueue_projection
    from mockazure.providers.graph.errors import GraphError
    from mockazure.providers.graph.evidence.permissions import EvidenceOperation, authorize_evidence

    await authorize_evidence(session, actor, EvidenceOperation("v1.0", "riskyUser", "POST"))
    if (
        action not in {"confirmCompromised", "dismiss"}
        or not 1 <= len(user_ids) <= 100
        or len(set(user_ids)) != len(user_ids)
    ):
        raise GraphError(400, "Request_BadRequest", "Invalid risk action")
    state = await lock_state(session, actor.tenant_id)
    when = await evidence_now(session, actor.tenant_id, now)
    users = []
    for identifier in sorted(user_ids):
        row = await current_version(session, actor.tenant_id, "riskyUser", identifier)
        if row is None:
            raise GraphError(404, "Request_ResourceNotFound", "Risky user not found")
        users.append(row)
    desired = (
        {
            "riskLevel": "high",
            "riskState": "confirmedCompromised",
            "riskDetail": "adminConfirmedUserCompromised",
        }
        if action == "confirmCompromised"
        else {
            "riskLevel": "none",
            "riskState": "dismissed",
            "riskDetail": "adminDismissedAllRiskForUser",
        }
    )
    changed = [row for row in users if any(row.payload.get(k) != v for k, v in desired.items())]
    if not changed:
        return
    source_id = uuid4()
    targets = []
    for row in changed:
        differences = [
            {
                "displayName": k,
                "oldValue": json.dumps(row.payload.get(k)),
                "newValue": json.dumps(v),
            }
            for k, v in desired.items()
            if row.payload.get(k) != v
        ]
        targets.append(
            {
                "id": str(row.record_id),
                "type": "User",
                "displayName": row.payload.get("userDisplayName"),
                "userPrincipalName": row.payload.get("userPrincipalName"),
                "modifiedProperties": differences,
            }
        )
    audit = {
        "id": str(uuid5(source_id, "mockazure:m3b:directoryAudit:0")),
        "activityDateTime": when.isoformat(),
        "activityDisplayName": "Update user",
        "category": "UserManagement",
        "correlationId": str(correlation_id),
        "loggedByService": "Identity Protection",
        "result": "success",
        "resultReason": None,
        "operationType": "Update",
        "initiatedBy": await actor_snapshot(session, actor),
        "targetResources": targets,
        "additionalDetails": [],
    }
    facts = RiskActionFacts(
        action=action, user_ids=tuple(row.record_id for row in changed), audit=audit
    )
    ref = SourceRef(tenant_id=actor.tenant_id, kind="risk_action", id=source_id)
    await append_source(
        session,
        SourceInput(
            ref=ref,
            occurred_at=when,
            available_at=when
            + timedelta(seconds=EvidenceSettings.model_validate(state.settings).delay_seconds),
            facts=facts.model_dump(mode="json"),
        ),
    )
    state.publication_revision += 1
    revision = state.publication_revision
    for row in changed:
        key = (actor.tenant_id, row.record_id)
        cutoff = await session.get(RiskActionCutoff, key)
        previous = cutoff.payload if cutoff and cutoff.generation == state.generation else {}
        data = dict(previous)
        if action == "dismiss":
            data["dismiss"] = {
                "occurred_at": when.isoformat(),
                "source_revision": state.source_revision,
            }
        cutoff = cutoff or RiskActionCutoff(tenant_id=key[0], user_id=key[1])
        cutoff.generation, cutoff.occurred_at, cutoff.source_revision = (
            state.generation,
            when,
            state.source_revision,
        )
        cutoff.action, cutoff.payload = action, data
        session.add(cutoff)
        await version_payload(
            session,
            row,
            {**row.payload, **desired, "riskLastUpdatedDateTime": when.isoformat()},
            revision,
        )
        if action == "dismiss":
            linked = list(
                await session.scalars(
                    select(EvidenceVersion).where(
                        EvidenceVersion.tenant_id == actor.tenant_id,
                        EvidenceVersion.subject_id == row.record_id,
                        EvidenceVersion.family.in_(["signIn", "riskDetection"]),
                        EvidenceVersion.valid_to.is_(None),
                    )
                )
            )
            for event in linked:
                original = await session.get(
                    EvidenceObservation, (actor.tenant_id, event.family, event.record_id)
                )
                source = await session.get(
                    EvidenceSource, (actor.tenant_id, original.source_kind, original.source_id)
                )
                payload = apply_risk_cutoff(
                    event.payload,
                    cutoff,
                    occurred_at=original.occurred_at,
                    source_revision=source.source_revision,
                    historical=source.kind == "historical",
                )
                await version_payload(session, event, payload, revision)
    await enqueue_projection(session, ref)
    await session.flush()


def unpublished_risk_not_due(now):
    """Do not consume retries or replace a visible aggregate before evidence availability."""
    from sqlalchemy import DateTime, String, case, cast, exists

    from mockazure.db.job_model import Job
    from mockazure.evidence.models import EvidenceState

    clock = case(
        (
            EvidenceState.settings["clock_mode"].as_string() == "fixed",
            cast(EvidenceState.settings["fixed_time"].as_string(), DateTime(timezone=True)),
        ),
        else_=now,
    )
    return exists(
        select(1)
        .select_from(EvidenceSource)
        .join(EvidenceState, EvidenceState.tenant_id == EvidenceSource.tenant_id)
        .where(
            Job.kind == "evidence.project",
            EvidenceSource.tenant_id == Job.tenant_id,
            EvidenceSource.kind == "risk",
            Job.source_key == "risk:" + cast(EvidenceSource.id, String),
            EvidenceSource.available_at > clock,
        )
    )
