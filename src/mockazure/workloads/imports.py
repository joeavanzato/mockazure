"""Bounded administrative workload records; adapters validate official payloads."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    @field_validator("*", mode="after")
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str):
            value.encode("utf-8")
            if any(ord(c) < 32 for c in value):
                raise ValueError("Control characters are unsupported")
        return value


class WorkloadKey(WorkloadInput):
    kind: str = Field(min_length=1, max_length=64)
    id: str = Field(min_length=1, max_length=512)

    def key(self):
        return self.kind, self.id


class WorkloadRecord(WorkloadKey):
    payload: dict
    content_id: UUID | None = None


class WorkloadEdge(WorkloadInput):
    source: WorkloadKey
    relation: str = Field(min_length=1, max_length=64)
    target: WorkloadKey

    def key(self):
        return (*self.source.key(), self.relation, *self.target.key())


class WorkloadImport(WorkloadInput):
    scope: str = Field(min_length=1, max_length=512)
    expected_revision: int | None = Field(default=None, ge=0, strict=True)
    records: list[WorkloadRecord] = Field(default_factory=list, max_length=1000)
    edges: list[WorkloadEdge] = Field(default_factory=list, max_length=1000)
    tombstones: list[WorkloadKey] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def coherent(self):
        if sum(map(len, (self.records, self.edges, self.tombstones))) > 1000:
            raise ValueError("Workload import exceeds 1000 records")
        for values in (self.records, self.edges, self.tombstones):
            if len({v.key() for v in values}) != len(values):
                raise ValueError("Duplicate workload identity")
        if {v.key() for v in self.records} & {v.key() for v in self.tombstones}:
            raise ValueError("Cannot upsert and delete the same record")
        return self


class ImportResult(WorkloadInput):
    revision: int
    count: int
