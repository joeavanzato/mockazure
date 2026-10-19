"""Owned syntax nodes. None contain executable Python or SQL."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Literal:
    value: object
    kind: str


@dataclass(frozen=True)
class Name:
    name: str


@dataclass(frozen=True)
class Access:
    source: object
    key: object


@dataclass(frozen=True)
class Call:
    name: str
    arguments: tuple


@dataclass(frozen=True)
class Unary:
    operator: str
    operand: object


@dataclass(frozen=True)
class Binary:
    operator: str
    left: object
    right: object


@dataclass(frozen=True)
class Values:
    values: tuple


@dataclass(frozen=True)
class Named:
    name: str | None
    expression: object


@dataclass(frozen=True)
class Operation:
    name: str
    arguments: tuple = ()
    options: tuple = ()


@dataclass(frozen=True)
class Pipeline:
    source: str
    operations: tuple


@dataclass(frozen=True)
class Binding:
    name: str
    value: object
