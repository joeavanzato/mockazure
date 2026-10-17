"""Internal evidence envelopes; counters are allocated only in storage."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

EvidenceFamily = Literal["signIn", "directoryAudit", "riskDetection", "riskyUser", "auditLogRecord"]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always", hide_input_in_errors=True
    )


class SourceRef(StrictModel):
    tenant_id: UUID
    kind: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_.]*$")
    id: UUID


class SourceInput(StrictModel):
    ref: SourceRef
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    facts: dict

    @model_validator(mode="after")
    def valid_times(self):
        if self.available_at < self.occurred_at:
            raise ValueError("Availability precedes occurrence")
        return self


class SourceEnvelope(SourceInput):
    source_revision: int = Field(gt=0)
    generation: UUID


class Projection(StrictModel):
    family: EvidenceFamily
    id: UUID
    occurred_at: AwareDatetime
    available_at: AwareDatetime
    payload: dict
    subject_id: UUID | None
    source_ref: SourceRef


class ProjectionBatch(StrictModel):
    source: SourceRef
    generation: UUID
    items: tuple[Projection, ...]
    receipt_status: Literal["projected", "suppressed", "legacy_incomplete"]


class PublishResult(StrictModel):
    revision: int
    record_ids: tuple[UUID, ...]
    already_applied: bool


class EvidenceSettings(StrictModel):
    delay_seconds: int = Field(default=5, ge=0, le=3600)
    retention_days: int = Field(default=30, ge=1, le=3650)
    clock_mode: Literal["wall", "fixed"] = "wall"
    fixed_time: AwareDatetime | None = None

    @model_validator(mode="after")
    def clock_has_time(self):
        if (self.clock_mode == "fixed") != (self.fixed_time is not None):
            raise ValueError("Only a fixed clock requires fixed_time")
        return self


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Aware time required")
    return value


class RiskObservation(StrictModel):
    user_id: UUID
    sign_in_id: UUID | None = None
    event_type: Literal["generic", "anonymizedIPAddress", "unfamiliarFeatures", "leakedCredentials"]
    level: Literal["low", "medium", "high"]
    occurred_at: AwareDatetime
    request_id: UUID


class RiskObservationResult(StrictModel):
    detection_id: UUID
    user_id: UUID
    revision: int
