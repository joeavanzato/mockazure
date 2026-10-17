from dataclasses import dataclass
from typing import Literal as LiteralType


@dataclass(frozen=True)
class Field:
    name: str


@dataclass(frozen=True)
class Literal:
    value: str | bool | int | float | None


@dataclass(frozen=True)
class Compare:
    op: str
    left: Field
    right: Literal


@dataclass(frozen=True)
class Boolean:
    op: LiteralType["and", "or"]
    children: tuple["FilterNode", ...]


@dataclass(frozen=True)
class In:
    field: Field
    values: tuple[Literal, ...]


@dataclass(frozen=True)
class Any:
    field: Field
    variable: str
    body: "FilterNode"


@dataclass(frozen=True)
class StartsWith:
    field: Field
    value: Literal


FilterNode = Compare | Boolean | In | Any | StartsWith


@dataclass(frozen=True)
class OrderTerm:
    field: str
    direction: LiteralType["asc", "desc"] = "asc"


@dataclass(frozen=True)
class ExpandSpec:
    field: str
    select: tuple[str, ...] | None = None


@dataclass(frozen=True)
class QuerySpec:
    filter: FilterNode | None = None
    select: tuple[str, ...] | None = None
    order: tuple[OrderTerm, ...] = ()
    top: int | None = None
    count: bool = False
    expand: tuple[ExpandSpec, ...] = ()
