"""Bounded public evidence datasets and streaming, pinned exports."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4, uuid5

from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import and_, delete, insert, or_, select
from sqlalchemy.exc import SQLAlchemyError

from mockazure.admin.errors import AdminError
from mockazure.db.job_model import Job
from mockazure.directory.types import PUBLIC_FIELDS
from mockazure.evidence.models import (
    EvidenceCursor,
    EvidenceObservation,
    EvidenceSource,
    EvidenceVersion,
)
from mockazure.evidence.repository import lock_state, source_hash
from mockazure.evidence.schemas import (
    EvidenceFamily,
    SourceInput,
    SourceRef,
    StrictModel,
    record_identity,
)
from mockazure.identity.models import User
from mockazure.providers.graph.evidence.contracts import public_fields
from mockazure.providers.graph.evidence.paging import snapshot_evidence
from mockazure.providers.graph.schema_reference import schema, validate_payload


class Provenance(StrictModel):
    source: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    seed: int | None = Field(default=None, ge=-(2**63), lt=2**63, strict=True)
    generator: str | None = Field(default=None, max_length=200)

    @field_validator("*")
    @classmethod
    def text(cls, value):
        if isinstance(value, str):
            if "\0" in value:
                raise ValueError("Invalid provenance")
            value.encode("utf-8")
        return value


NESTED = {
    "status": {"errorCode", "failureReason", "additionalDetails"},
    "deviceDetail": {
        "deviceId",
        "displayName",
        "operatingSystem",
        "browser",
        "isCompliant",
        "isManaged",
        "trustType",
    },
    "location": {"city", "state", "countryOrRegion", "geoCoordinates"},
    "geoCoordinates": {"latitude", "longitude", "altitude"},
    "initiatedBy": {"user", "app"},
    "user": {"id", "displayName", "userPrincipalName", "ipAddress"},
    "app": {"appId", "displayName", "servicePrincipalId", "servicePrincipalName"},
    "targetResources": {
        "id",
        "displayName",
        "type",
        "userPrincipalName",
        "groupType",
        "modifiedProperties",
    },
    "modifiedProperties": {"displayName", "oldValue", "newValue"},
    "additionalDetails": {"key", "value"},
    "authenticationDetails": {
        "authenticationStepDateTime",
        "authenticationMethod",
        "authenticationMethodDetail",
        "succeeded",
        "authenticationStepResultDetail",
        "authenticationStepRequirement",
    },
}


def validate_public_payload(version, family, payload):
    if family == "activityLog":
        from mockazure.providers.arm.activity import validate_activity_payload

        if version != "2015-04-01":
            raise ValueError("Activity Logs require their ARM API version")
        validate_activity_payload(payload)
        return
    if family == "auditLogRecord":
        from mockazure.mail.audit_schemas import validate_audit_record

        validate_audit_record(payload)
        return
    validate_payload(version, family, payload)
    if payload.keys() - public_fields(version, family) - {"@odata.type"}:
        raise ValueError("Property is outside the declared evidence profile")

    def nested(name, value):
        if value is None:
            return
        if isinstance(value, list):
            for item in value:
                nested(name, item)
        elif isinstance(value, dict):
            if name not in NESTED or value.keys() - NESTED[name]:
                raise ValueError("Unsupported nested evidence properties")
            for child, item in value.items():
                if isinstance(item, (dict, list)):
                    nested(child, item)

    for name, value in payload.items():
        if name in NESTED:
            nested(name, value)
    for target in payload.get("targetResources") or []:
        security_kind = {
            "Application": "application",
            "ServicePrincipal": "servicePrincipal",
            "Device": "device",
            "oAuth2PermissionGrant": "oAuth2PermissionGrant",
            "appRoleAssignment": "appRoleAssignment",
            "unifiedRoleAssignment": "unifiedRoleAssignment",
            "conditionalAccessPolicy": "conditionalAccessPolicy",
            "ipNamedLocation": "ipNamedLocation",
        }.get(target.get("type"))
        if security_kind:
            from mockazure.security.audit import safe

            for change in target.get("modifiedProperties") or []:
                name = change["displayName"]
                if name not in schema(version)["types"][security_kind][
                    "properties"
                ] and name not in {"owners", "registeredOwners"}:
                    raise ValueError("Undeclared security audit property")
                for side in ("oldValue", "newValue"):
                    value = json.loads(change[side]) if change.get(side) is not None else None
                    if value is None:
                        continue
                    if safe(value) != value:
                        raise ValueError("Private credential data is not public evidence")
                    if name in {"owners", "registeredOwners"}:
                        if not isinstance(value, list) or len(value) > 100:
                            raise ValueError("Invalid security owner difference")
                        for identifier in value:
                            UUID(identifier)
                    else:
                        validate_payload(version, security_kind, {name: value}, partial=True)
            continue
        for change in target.get("modifiedProperties") or []:
            name = change["displayName"]
            allowed = set().union(*PUBLIC_FIELDS.values()) | {
                "members",
                "owners",
                "riskLevel",
                "riskState",
                "riskDetail",
            }
            if name not in allowed:
                raise ValueError("Audit change is outside the public directory/risk profile")
            for side in ("oldValue", "newValue"):
                raw = change.get(side)
                if raw is None:
                    continue
                value = json.loads(raw)
                # Audit differences describe absence on creation/deletion; they
                # are JSON strings, not a resource instance with a null property.
                if value is None:
                    continue
                if name in {"members", "owners"}:
                    if value is not None:
                        if not isinstance(value, list) or len(value) > 10000:
                            raise ValueError("Invalid relationship audit difference")
                        for identifier in value:
                            UUID(identifier)
                else:
                    kind = next(
                        (
                            kind
                            for kind in ("user", "group", "riskyUser")
                            if name in schema(version)["types"][kind]["properties"]
                        ),
                        None,
                    )
                    if kind is None:
                        raise ValueError("Unsupported audit difference")
                    validate_payload(version, kind, {name: value}, partial=True)


class EvidenceRecord(StrictModel):
    family: EvidenceFamily
    api_version: Literal["v1.0", "beta", "2015-04-01"] = "v1.0"
    payload: dict
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    origin: Literal["historical"] = "historical"
    entitlement_snapshot: Literal["P1", "P2"] | None = None
    provenance: Provenance | None = None
    sign_in_id: UUID | None = None

    @model_validator(mode="after")
    def validated(self):
        validate_public_payload(self.api_version, self.family, self.payload)
        record_identity(self.family, self.payload)
        if self.available_at < self.occurred_at:
            raise ValueError("Availability precedes occurrence")
        if self.sign_in_id and self.family != "riskDetection":
            raise ValueError("Only detections may link a sign-in")
        if self.family == "auditLogRecord":
            from mockazure.mail.audit_schemas import validate_audit_record

            validate_audit_record(self.payload, occurred_at=self.occurred_at)
        if self.family == "activityLog":
            from mockazure.providers.arm.activity import validate_activity_payload

            validate_activity_payload(
                self.payload, occurred_at=self.occurred_at, available_at=self.available_at
            )
        subject = self.subject_id()
        if subject:
            UUID(subject)
        return self

    def subject_id(self):
        if self.family in {"auditLogRecord", "activityLog"}:
            # UAL actors include systems and service principals, not only directory users.
            return None
        return (
            self.payload["id"] if self.family == "riskyUser" else self.payload.get("userId") or None
        )


class EvidenceDataset(StrictModel):
    schema_version: Literal[1]
    provenance: Provenance
    records: list[EvidenceRecord] = Field(max_length=10000)


class ImportResult(StrictModel):
    inserted: int
    unchanged: int
    record_ids: tuple[UUID, ...]


class EvidenceExportQuery(StrictModel):
    family: EvidenceFamily | None = None
    from_time: AwareDatetime | None = None
    to_time: AwareDatetime | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.from_time and self.to_time and self.from_time > self.to_time:
            raise ValueError("Invalid export time range")
        return self


async def import_evidence(session, tenant_id, dataset):
    dataset = EvidenceDataset.model_validate(dataset.model_dump())
    for record in dataset.records:
        if record.family == "auditLogRecord":
            from mockazure.mail.audit_schemas import validate_audit_record

            try:
                validate_audit_record(
                    record.payload, tenant=tenant_id, occurred_at=record.occurred_at
                )
            except ValueError:
                raise AdminError(
                    422, "InvalidAuditTenant", "Audit organization must match the tenant"
                ) from None
    from mockazure.providers.arm.activity import validate_activity_payload
    from mockazure.providers.arm.models import Subscription

    activity_subscriptions = set()
    for record in dataset.records:
        if record.family == "activityLog":
            try:
                validate_activity_payload(
                    record.payload,
                    tenant=tenant_id,
                    occurred_at=record.occurred_at,
                    available_at=record.available_at,
                )
            except ValueError:
                raise AdminError(
                    422, "InvalidActivityTenant", "Activity tenant does not match"
                ) from None
            activity_subscriptions.add(UUID(record.payload["subscriptionId"]))
    owned = set(
        await session.scalars(
            select(Subscription.id).where(
                Subscription.tenant_id == tenant_id, Subscription.id.in_(activity_subscriptions)
            )
        )
    )
    if owned != activity_subscriptions:
        raise AdminError(
            422, "InvalidActivitySubscription", "Activity requires an owned subscription"
        )
    state = await lock_state(session, tenant_id)
    subjects = {UUID(record.subject_id()) for record in dataset.records if record.subject_id()}
    known = {
        row.id: row for row in await session.scalars(select(User).where(User.id.in_(subjects)))
    }
    if any(row.tenant_id != tenant_id for row in known.values()):
        raise AdminError(
            422, "ForeignEvidenceSubject", "Evidence subject belongs to another tenant"
        )
    payloads = {(r.family, record_identity(r.family, r.payload)): r for r in dataset.records}
    link_ids = {r.sign_in_id for r in dataset.records if r.sign_in_id}
    links = {
        r.record_id: r.subject_id
        for r in await session.scalars(
            select(EvidenceObservation).where(
                EvidenceObservation.tenant_id == tenant_id,
                EvidenceObservation.family == "signIn",
                EvidenceObservation.record_id.in_(link_ids),
            )
        )
    }
    for record in dataset.records:
        if record.sign_in_id:
            target = payloads.get(("signIn", record.sign_in_id))
            linked = (
                UUID(target.subject_id())
                if target and target.subject_id()
                else links.get(record.sign_in_id)
            )
            if linked is None or str(linked) != record.subject_id():
                raise AdminError(
                    422, "InvalidEvidenceLink", "Sign-in link does not belong to the subject"
                )
    inputs = []
    for record in dataset.records:
        subject = record.subject_id()
        user = known.get(UUID(subject)) if subject else None
        facts = {
            "family": record.family,
            "version": record.api_version,
            "payload": record.payload,
            "provenance": (record.provenance or dataset.provenance).model_dump(mode="json"),
            "entitlement_snapshot": record.entitlement_snapshot,
            "orphaned_subject": bool(subject and (user is None or user.deleted_at is not None)),
            "subject_id": subject,
            "sign_in_id": str(record.sign_in_id) if record.sign_in_id else None,
        }
        ref = SourceRef(
            tenant_id=tenant_id,
            kind="historical",
            id=uuid5(
                record_identity(record.family, record.payload),
                "mockazure:historical:" + record.family,
            ),
        )
        inputs.append(
            SourceInput(
                ref=ref,
                occurred_at=record.occurred_at,
                available_at=record.available_at,
                facts=facts,
            )
        )
    existing = {
        row.id: row
        for row in await session.scalars(
            select(EvidenceSource).where(
                EvidenceSource.tenant_id == tenant_id,
                EvidenceSource.kind == "historical",
                EvidenceSource.id.in_([v.ref.id for v in inputs]),
            )
        )
    }
    observed = {
        (r.family, r.record_id)
        for r in await session.scalars(
            select(EvidenceObservation).where(
                EvidenceObservation.tenant_id == tenant_id,
                EvidenceObservation.record_id.in_(
                    [record_identity(r.family, r.payload) for r in dataset.records]
                ),
            )
        )
    }
    new, unchanged, sources, jobs = {}, 0, [], []
    for record, value in zip(dataset.records, inputs, strict=True):
        identifier = record_identity(record.family, record.payload)
        prior = existing.get(value.ref.id)
        # The orphan marker is captured at first import, not recomputed on an identical replay.
        if prior:
            value = value.model_copy(
                update={
                    "facts": {
                        **value.facts,
                        "orphaned_subject": prior.facts.get("orphaned_subject", False),
                    }
                }
            )
        digest = source_hash(value)
        prior_hash = prior.content_hash if prior else new.get(value.ref.id)
        if prior_hash:
            if prior_hash != digest:
                raise AdminError(
                    409, "EvidenceConflict", "Record ID has different evidence content"
                )
            unchanged += 1
            continue
        if (record.family, identifier) in observed:
            raise AdminError(409, "EvidenceConflict", "Record ID already belongs to another source")
        new[value.ref.id] = digest
        state.source_revision += 1
        sources.append(
            dict(
                tenant_id=tenant_id,
                kind="historical",
                id=value.ref.id,
                generation=state.generation,
                source_revision=state.source_revision,
                occurred_at=value.occurred_at,
                available_at=value.available_at,
                facts=value.facts,
                content_hash=digest,
            )
        )
        jobs.append(
            dict(
                id=uuid4(),
                tenant_id=tenant_id,
                kind="evidence.project",
                source_key="historical:" + str(value.ref.id),
                payload={
                    "source": value.ref.model_dump(mode="json"),
                    "generation": str(state.generation),
                },
            )
        )
    if sources:
        await session.execute(insert(EvidenceSource), sources)
        await session.execute(insert(Job), jobs)
    await session.flush()
    return ImportResult(
        inserted=len(sources),
        unchanged=unchanged,
        record_ids=tuple(
            dict.fromkeys(record_identity(r.family, r.payload) for r in dataset.records)
        ),
    )


EXPORT_PROVENANCE = Provenance(source="MockAzure snapshot", generator="mockazure-evidence-v1")


async def export_evidence(session_factory, tenant_id, query):
    query = EvidenceExportQuery.model_validate(query.model_dump())
    wall = datetime.now(UTC)
    pin = hashlib.sha256(uuid4().bytes).hexdigest()
    async with session_factory.begin() as session:
        snapshot = await snapshot_evidence(session, tenant_id, wall)
        session.add(
            EvidenceCursor(
                token_hash=pin,
                tenant_id=tenant_id,
                generation=snapshot.generation,
                revision=snapshot.revision,
                evidence_time=snapshot.evidence_time,
                retention_cutoff=snapshot.retention_cutoff,
                expires_at=snapshot.expires_at,
                state={"kind": "export"},
            )
        )

    async def release():
        try:
            async with session_factory.begin() as session:
                await session.execute(
                    delete(EvidenceCursor).where(EvidenceCursor.token_hash == pin)
                )
        except SQLAlchemyError:
            pass  # A lost connection leaves a bounded one-hour expiry pin.

    try:
        yield (
            '{"schema_version":1,"provenance":'
            + EXPORT_PROVENANCE.model_dump_json()
            + ',"records":['
        ).encode()
        last = None
        emitted = False
        while True:
            async with session_factory() as session:
                conditions = [
                    EvidenceVersion.tenant_id == tenant_id,
                    EvidenceVersion.generation == snapshot.generation,
                    EvidenceVersion.valid_from <= snapshot.revision,
                    or_(
                        EvidenceVersion.valid_to.is_(None),
                        EvidenceVersion.valid_to > snapshot.revision,
                    ),
                    EvidenceVersion.available_at <= snapshot.evidence_time,
                    or_(
                        EvidenceVersion.family == "riskyUser",
                        EvidenceVersion.event_time >= snapshot.retention_cutoff,
                    ),
                    EvidenceVersion.event_time <= snapshot.evidence_time,
                ]
                from mockazure.providers.arm.activity import datetime_ticks

                activity_conditions = [
                    EvidenceVersion.arm_event_ticks <= datetime_ticks(snapshot.evidence_time),
                    EvidenceVersion.arm_available_ticks <= datetime_ticks(snapshot.evidence_time),
                ]
                if query.from_time:
                    activity_conditions.append(
                        EvidenceVersion.arm_event_ticks >= datetime_ticks(query.from_time)
                    )
                if query.to_time:
                    activity_conditions.append(
                        EvidenceVersion.arm_event_ticks <= datetime_ticks(query.to_time)
                    )
                conditions.append(
                    or_(EvidenceVersion.family != "activityLog", and_(*activity_conditions))
                )
                if query.family:
                    conditions.append(EvidenceVersion.family == query.family)
                if query.from_time:
                    conditions.append(EvidenceVersion.event_time >= query.from_time)
                if query.to_time:
                    conditions.append(EvidenceVersion.event_time <= query.to_time)
                if last:
                    conditions.append(
                        or_(
                            EvidenceVersion.family > last[0],
                            and_(
                                EvidenceVersion.family == last[0],
                                EvidenceVersion.record_id > last[1],
                            ),
                        )
                    )
                rows = (
                    await session.execute(
                        select(EvidenceVersion, EvidenceSource, EvidenceObservation)
                        .join(
                            EvidenceSource,
                            and_(
                                EvidenceSource.tenant_id == EvidenceVersion.tenant_id,
                                EvidenceSource.kind == EvidenceVersion.source_kind,
                                EvidenceSource.id == EvidenceVersion.source_id,
                            ),
                        )
                        .join(
                            EvidenceObservation,
                            and_(
                                EvidenceObservation.tenant_id == EvidenceVersion.tenant_id,
                                EvidenceObservation.family == EvidenceVersion.family,
                                EvidenceObservation.record_id == EvidenceVersion.record_id,
                            ),
                        )
                        .where(*conditions)
                        .order_by(EvidenceVersion.family, EvidenceVersion.record_id)
                        .limit(500)
                    )
                ).all()
            if not rows:
                break
            for row, source, observation in rows:
                provenance = source.facts.get("provenance")
                if not isinstance(provenance, dict):
                    provenance = Provenance(
                        source=provenance or "native:" + source.kind, generator="mockazure"
                    ).model_dump(mode="json")
                record = EvidenceRecord(
                    family=row.family,
                    api_version=source.facts.get(
                        "version",
                        "2015-04-01"
                        if row.family == "activityLog"
                        else "v1.0"
                        if source.kind in {"security", "mail"}
                        else "beta",
                    ),
                    payload=row.payload,
                    occurred_at=observation.occurred_at,
                    available_at=observation.available_at,
                    provenance=Provenance.model_validate(provenance),
                    entitlement_snapshot=source.facts.get("entitlement_snapshot"),
                    sign_in_id=(
                        source.facts.get("sign_in_id")
                        or source.facts.get("input", {}).get("sign_in_id")
                    )
                    if row.family == "riskDetection"
                    else None,
                )
                yield (b"," if emitted else b"") + record.model_dump_json().encode()
                emitted = True
            last = (rows[-1][0].family, rows[-1][0].record_id)
        yield b"]}"
    finally:
        await asyncio.shield(release())
