"""Query interfaces and explicit emulator execution limits."""

from dataclasses import dataclass, field
from uuid import UUID


class QueryError(ValueError):
    def __init__(self, message, code="BadRequest"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class QueryPlan:
    profile: str
    nodes: tuple
    parameters: dict = field(default_factory=dict)


@dataclass
class QueryResult:
    columns: list[tuple[str, str]]
    rows: list[list]
    truncated: bool = False


@dataclass(frozen=True)
class QueryScope:
    tenant_id: UUID
    subscription_ids: tuple[str, ...] = ()
    workspace_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class QueryLimits:
    seconds: float = 30
    rows: int = 100000
    bytes: int = 64 * 1024 * 1024
    expanded: int = 1000000
