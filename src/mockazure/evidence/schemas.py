"""Internal evidence envelopes; counters are allocated only in storage."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

EvidenceFamily = Literal[
    "signIn", "directoryAudit", "riskDetection", "riskyUser", "auditLogRecord", "activityLog"
]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always", hide_input_in_errors=True
    )


class OperationEvidenceContract(StrictModel):
    operation_key: str = Field(min_length=1, max_length=200)
    source_kind: Literal[
        "none", "authentication", "directory", "security", "mail", "workload", "arm", "service"
    ]
    outcome: Literal["success", "success_or_failure", "none"]
    destinations: tuple[
        Literal[
            "signIn",
            "directoryAudit",
            "auditLogRecord",
            "activityLog",
            "StorageBlobLogs",
            "StorageFileLogs",
            "StorageTableLogs",
            "StorageQueueLogs",
            "AZKVAuditLogs",
        ],
        ...,
    ]
    schema_refs: tuple[str, ...] = Field(min_length=1)
    native: bool
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def disposition(self):
        if not self.destinations and not self.limitations:
            raise ValueError("No-evidence dispositions require an explicit reason")
        if self.destinations and (not self.native or self.source_kind == "none"):
            raise ValueError("Destinations require a native source")
        if len(set(self.destinations)) != len(self.destinations):
            raise ValueError("Duplicate evidence destination")
        return self


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


def record_identity(family, payload):
    """ARM EventData has an ARM id and a separate UUID eventDataId."""
    return UUID(payload["eventDataId"] if family == "activityLog" else payload["id"])
