from dataclasses import dataclass
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
class ClaimedJob:
    id: UUID
    owner: UUID
    attempt: int
    payload: ExportSnapshot
