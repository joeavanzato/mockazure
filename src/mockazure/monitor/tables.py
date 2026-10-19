"""Typed table definitions transcribed from pinned Microsoft table references."""

import json
import math
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path


@lru_cache
def catalog():
    return json.loads((Path(__file__).parent / "data/tables.json").read_text(encoding="utf-8"))


def instant(value):
    if not isinstance(value, str):
        raise ValueError("UTC timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamp requires a timezone")
    return result.astimezone(UTC)


def wire_time(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def validate_row(table, row):
    if table not in catalog() or not isinstance(row, dict):
        raise ValueError("Unsupported log table or row")
    columns = {c["name"]: c["type"] for c in catalog()[table]["columns"]}
    if set(row) - columns.keys() or "TimeGenerated" not in row:
        raise ValueError("Unknown log column or missing TimeGenerated")
    output = {}
    for name, value in row.items():
        kind = columns[name]
        if value is None:
            if name == "TimeGenerated":
                raise ValueError("TimeGenerated cannot be null")
        elif kind == "datetime":
            value = wire_time(instant(value))
        elif kind in {"int", "long"}:
            bits = 32 if kind == "int" else 64
            if type(value) is not int or not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                raise ValueError("Invalid integer log value")
        elif kind == "real":
            if type(value) not in {int, float} or not math.isfinite(value):
                raise ValueError("Invalid numeric log value")
            value = float(value)
        elif kind == "bool":
            if type(value) is not bool:
                raise ValueError("Invalid boolean log value")
        elif kind == "guid":
            from uuid import UUID

            value = str(UUID(value))
        elif kind == "string":
            if not isinstance(value, str):
                raise ValueError("Invalid string log value")
        elif kind != "dynamic":
            raise ValueError("Unsupported column type")
        output[name] = value
    raw = json.dumps(output, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode()) > 1024 * 1024 or "\\u0000" in raw:
        raise ValueError("Log row exceeds local limits")
    return json.loads(raw)


def typed_row(table, payload):
    return [
        instant(payload[c["name"]])
        if c["type"] == "datetime" and payload.get(c["name"]) is not None
        else payload.get(c["name"])
        if payload.get(c["name"]) is not None
        else ""
        if c["type"] == "string"
        else None
        for c in catalog()[table]["columns"]
    ]
