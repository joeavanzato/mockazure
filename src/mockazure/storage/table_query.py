"""Table EDM values and a bounded OData predicate grammar (never executable SQL)."""

import base64
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, Float, LargeBinary, and_, case, cast, func, not_, or_

from mockazure.providers.storage.errors import StorageError

SYSTEM = {"PartitionKey", "RowKey", "Timestamp"}


def invalid(message="Invalid Table property or query", code="InvalidInput"):
    raise StorageError(400, code, message)


def check_key(value):
    if (
        not isinstance(value, str)
        or len(value) > 1024
        or any(c in "/\\#?" or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        invalid("Invalid PartitionKey or RowKey")
    try:
        value.encode("utf-16-le")
    except UnicodeError:
        invalid()
    return value


def property_name(name):
    if not isinstance(name, str) or len(name) > 255 or not name.isidentifier():
        invalid("Invalid Table property name")


def date_value(value):
    if not isinstance(value, str):
        invalid()
    match = re.fullmatch(
        r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,7}))?(Z|[+-]\d\d:\d\d)?", value
    )
    if not match:
        invalid("Invalid Edm.DateTime")
    try:
        base = datetime.fromisoformat(
            match[1] + (match[3] or "+00:00").replace("Z", "+00:00")
        ).astimezone(UTC)
        if base.year < 1601:
            invalid("Edm.DateTime precedes 1601")
    except (ValueError, OverflowError):
        invalid("Invalid Edm.DateTime")
    # Preserve the seventh fractional digit which Python datetime would truncate.
    return base.strftime("%Y-%m-%dT%H:%M:%S") + "." + (match[2] or "").ljust(7, "0") + "Z"


def normalize_properties(body):
    if not isinstance(body, dict):
        invalid()
    result = {}
    for name, value in body.items():
        if name in SYSTEM or name.startswith("odata."):
            continue
        if name.endswith("@odata.type"):
            if name[:-11] not in body:
                invalid("Type annotation has no property")
            continue
        property_name(name)
        if value is None:
            continue
        inferred = {
            str: "Edm.String",
            bool: "Edm.Boolean",
            int: "Edm.Int32",
            float: "Edm.Double",
        }.get(type(value))
        kind = body.get(name + "@odata.type", inferred)
        if kind == "Edm.String":
            if not isinstance(value, str) or len(value.encode("utf-16-le")) > 65536:
                invalid("String exceeds 64 KiB", "PropertyValueTooLarge")
        elif kind == "Edm.Boolean":
            if type(value) is not bool:
                invalid()
        elif kind == "Edm.Int32":
            if type(value) is not int or not -(2**31) <= value < 2**31:
                invalid()
        elif kind == "Edm.Int64":
            if (
                not isinstance(value, str)
                or not re.fullmatch(r"[+-]?\d{1,20}", value)
                or not -(2**63) <= int(value) < 2**63
            ):
                invalid()
            value = str(int(value))
        elif kind == "Edm.Double":
            if isinstance(value, str):
                if value not in {"NaN", "Infinity", "-Infinity"}:
                    invalid()
            elif type(value) not in {int, float} or not math.isfinite(value):
                invalid()
            else:
                value = float(value) if value else 0.0
        elif kind == "Edm.Binary":
            try:
                if (
                    not isinstance(value, str)
                    or len(base64.b64decode(value, validate=True)) > 65536
                ):
                    invalid()
                value = base64.b64encode(base64.b64decode(value, validate=True)).decode()
            except (ValueError, TypeError):
                invalid("Invalid Edm.Binary")
        elif kind == "Edm.Guid":
            try:
                value = str(UUID(value))
            except (ValueError, TypeError, AttributeError):
                invalid("Invalid Edm.Guid")
        elif kind == "Edm.DateTime":
            value = date_value(value)
        else:
            invalid("Unsupported EDM property type")
        result[name] = {"type": kind, "value": value}
    if len(result) > 252:
        invalid("Entity has more than 252 custom properties", "TooManyProperties")
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 1048576:
        raise StorageError(
            413, "EntityTooLarge", "Entity exceeds the local 1 MiB representation limit"
        )
    return result


def wire_properties(properties, mode="minimalmetadata"):
    result = {}
    for name, value in properties.items():
        kind = value["type"]
        result[name] = value["value"]
        if mode != "nometadata" and (
            kind in {"Edm.Int64", "Edm.Binary", "Edm.DateTime", "Edm.Guid"}
            or (kind == "Edm.Double" and isinstance(value["value"], str))
        ):
            result[name + "@odata.type"] = kind
    return result


@dataclass(frozen=True)
class Compare:
    name: str
    operator: str
    kind: str
    value: object


TOKEN = re.compile(
    r"\s*(datetime'(?:[^']|'')*'|guid'(?:[^']|'')*'|[Xx]'[0-9A-Fa-f]*'|'(?:[^']|'')*'|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?[LlDdFf]?|[\w]+|[()])",
    re.UNICODE,
)


def parse_table_filter(text):
    if not text:
        return None
    if len(text) > 8192:
        invalid("Table filter exceeds local limit")
    tokens = []
    pos = 0
    while pos < len(text):
        if not text[pos:].strip():
            break
        match = TOKEN.match(text, pos)
        if not match:
            invalid("Unsupported Table filter")
        tokens.append(match[1])
        pos = match.end()
    index = 0
    comparisons = 0

    def take():
        nonlocal index
        if index >= len(tokens):
            invalid("Incomplete Table filter")
        token = tokens[index]
        index += 1
        return token

    def atom(depth):
        nonlocal comparisons
        if depth > 32:
            invalid("Table filter nesting exceeds local limit")
        if index < len(tokens) and tokens[index] == "not":
            take()
            return ("not", atom(depth + 1))
        if index < len(tokens) and tokens[index] == "(":
            take()
            node = expression(depth + 1)
            if take() != ")":
                invalid()
            return node
        name = take()
        property_name(name)
        operator = take()
        value = take()
        if operator not in {"eq", "ne", "gt", "ge", "lt", "le"}:
            invalid("Unsupported Table comparison")
        comparisons += 1
        if comparisons > 15:
            invalid("At most 15 comparisons are supported")
        if value.startswith("'"):
            kind = "Edm.String"
            value = value[1:-1].replace("''", "'")
        elif value in {"true", "false"}:
            kind = "Edm.Boolean"
            value = value == "true"
        elif value.startswith("datetime'"):
            kind = "Edm.DateTime"
            value = date_value(value[9:-1])
        elif value.startswith("guid'"):
            try:
                value = str(UUID(value[5:-1]))
                kind = "Edm.Guid"
            except ValueError:
                invalid()
        elif value[:2].lower() == "x'":
            try:
                value = base64.b64encode(bytes.fromhex(value[2:-1])).decode()
                kind = "Edm.Binary"
            except ValueError:
                invalid()
        else:
            try:
                if value.lower().endswith("l"):
                    kind = "Edm.Int64"
                    value = int(value[:-1])
                elif "." in value or "e" in value.lower() or value[-1:].lower() in {"d", "f"}:
                    kind = "Edm.Double"
                    value = float(value.rstrip("dDfF"))
                else:
                    kind = "Edm.Int32"
                    value = int(value)
            except ValueError:
                invalid("Invalid Table literal")
            if kind == "Edm.Int64" and not -(2**63) <= value < 2**63:
                invalid()
            if kind == "Edm.Int32" and not -(2**31) <= value < 2**31:
                invalid()
            if kind == "Edm.Double" and not math.isfinite(value):
                invalid()
        return Compare(name, operator, kind, value)

    def conjunction(depth):
        node = atom(depth)
        while index < len(tokens) and tokens[index] == "and":
            take()
            node = ("and", node, atom(depth))
        return node

    def expression(depth):
        node = conjunction(depth)
        while index < len(tokens) and tokens[index] == "or":
            take()
            node = ("or", node, conjunction(depth))
        return node

    result = expression(0)
    if index != len(tokens):
        invalid("Trailing Table filter tokens")
    return result


def compile_filter(node, model, *, tables=False):
    if node is None:
        return True
    if isinstance(node, tuple):
        if node[0] == "not":
            return not_(compile_filter(node[1], model, tables=tables))
        fn = and_ if node[0] == "and" else or_
        return fn(*(compile_filter(n, model, tables=tables) for n in node[1:]))
    if tables:
        if node.name != "TableName" or node.kind != "Edm.String":
            invalid("Table query requires TableName")
        column = model.name.collate("C")
        guard = True
    elif node.name in SYSTEM:
        if node.name == "Timestamp":
            if node.kind != "Edm.DateTime":
                invalid()
            column = model.timestamp_text
            guard = True
        else:
            if node.kind != "Edm.String":
                invalid()
            column = getattr(
                model, "partition_key" if node.name == "PartitionKey" else "row_key"
            ).collate("C")
            guard = True
    else:
        kind = model.properties[node.name]["type"].astext
        value = model.properties[node.name]["value"].astext
        guard = kind == node.kind
        column = case((guard, value), else_=None)
        if node.kind in {"Edm.Int32", "Edm.Int64"}:
            column = cast(column, BigInteger)
        elif node.kind == "Edm.Double":
            column = cast(column, Float)
        elif node.kind == "Edm.Boolean":
            column = cast(column, Boolean)
        elif node.kind == "Edm.Binary":
            column = func.decode(column, "base64", type_=LargeBinary)
        else:
            column = column.collate("C")
    result = {
        "eq": column.__eq__,
        "ne": column.__ne__,
        "gt": column.__gt__,
        "ge": column.__ge__,
        "lt": column.__lt__,
        "le": column.__le__,
    }[node.operator](base64.b64decode(node.value) if node.kind == "Edm.Binary" else node.value)
    # Preserve SQL UNKNOWN for absent/incompatible properties, including under NOT.
    return result
