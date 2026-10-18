"""Internal service boundaries; public protocols retain their own serializers."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ResourceRef:
    tenant_id: UUID
    scope: str
    resource_type: str


@dataclass(frozen=True)
class MutationResult:
    status: int
    body: dict | None
    headers: dict[str, str]


@dataclass(frozen=True)
class ServiceEvent:
    id: UUID
    tenant_id: UUID
    resource_id: str
    service: str
    category: str
    operation: str
    occurred_at: datetime
    payload: dict
