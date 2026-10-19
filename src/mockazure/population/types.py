"""Shared contracts for the CLI, console and service-specific adapters."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from mockazure.config import Settings
from mockazure.db.engine import Database


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class OperationDescriptor(Input):
    key: str
    mode: Literal["fixture", "service"]
    method: str
    path_template: str
    api_version: str
    input_schema: dict = Field(default_factory=dict)
    identity_required: bool = False
    destructive: bool = False
    replay_policy: Literal["idempotent", "reconcilable", "manual"] = "reconcilable"


class FamilyDescriptor(Input):
    key: str
    service: str
    api_path: str
    display_fields: list[str]
    relationships: list[str] = Field(default_factory=list)
    operations: list[OperationDescriptor] = Field(default_factory=list)
    contract_refs: list[str]
    limitations: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class AdapterContext:
    database: Database
    settings: Settings
    tenant_id: UUID
    identity_ref: str | None = None


class ResourceQuery(Input):
    limit: int = Field(default=100, ge=1, le=500, strict=True)
    continuation: str | None = Field(default=None, max_length=8192)
    filters: dict[str, JsonValue] = Field(default_factory=dict)
    parent: str | None = Field(default=None, max_length=2048)
    identifier: str | None = Field(default=None, max_length=2048)


class ResourceItem(Input):
    id: str
    properties: dict[str, JsonValue]
    version: str
    parent: str | None = None


class ResourcePage(Input):
    items: list[ResourceItem]
    continuation: str | None = None


class Mutation(Input):
    operation: str
    logical_key: str = Field(min_length=1, max_length=256)
    payload: dict[str, JsonValue]
    expected_version: str | None = None
    identity_ref: str | None = None
    parent: str | None = None


class Inspection(Input):
    actual_id: str | None
    version: str | None
    exists: bool
    conflicts: list[str] = Field(default_factory=list)


class BatchResult(Input):
    items: list[ResourceItem]
    continuation: str | None = None
    effects: dict[str, int]


class VerificationResult(Input):
    status: Literal["succeeded", "failed", "pending"]
    expected: int
    actual: int
    errors: list[str] = Field(default_factory=list)


class ServiceAdapter(Protocol):
    def describe(self) -> FamilyDescriptor: ...
    def validate(self, mutation: Mutation) -> None: ...
    async def inspect(self, ctx: AdapterContext, mutation: Mutation) -> Inspection: ...
    async def browse(self, ctx: AdapterContext, query: ResourceQuery) -> ResourcePage: ...
    async def apply(self, ctx: AdapterContext, mutations: list[Mutation]) -> BatchResult: ...
    async def verify(self, ctx: AdapterContext, result: BatchResult) -> VerificationResult: ...
