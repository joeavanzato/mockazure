from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict

from mockazure.tenants.schemas import TenantCreate


class ExportSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    schema_version: Literal[1] = 1
    source_tenant_id: UUID
    captured_at: AwareDatetime
    tenant: TenantCreate


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    snapshot: ExportSnapshot
    replacement: TenantCreate | None = None


class JobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    tenant_id: UUID
    kind: Literal["tenant.export"]
    state: Literal["pending", "running", "succeeded", "failed"]
    attempts: int
    error_code: str | None


@dataclass(frozen=True)
class JobClaim:
    id: UUID
    tenant_id: UUID
    kind: str
    owner: UUID
    attempt: int
    epoch: int
    lease_until: datetime
    payload: dict


@dataclass(frozen=True)
class JobResult:
    state: str
    result: dict | None
    error_code: str | None


# Existing Python imports keep working; the internal claim is now kind-aware.
ClaimedJob = JobClaim
