"""Strict typed rows, bounded JSON payloads and server-owned source attribution."""

import json
import math
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mockazure.hunting.contracts import table_contract


class HuntingProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["fixture", "native"]
    source_ref: str = Field(min_length=1, max_length=256)
    generator_version: str = Field(min_length=1, max_length=128)


def validate_row(table, row):
    contract = table_contract(table)
    if not isinstance(row, dict) or not row.keys() <= contract.columns.keys():
        raise ValueError("Unknown hunting columns")
    if not isinstance(row.get("Timestamp"), str):
        raise ValueError("Hunting rows require a timestamp")
    try:
        encoded = json.dumps(row, allow_nan=False, ensure_ascii=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Invalid hunting JSON") from None
    if len(encoded) > 1024 * 1024 or b"\\u0000" in encoded:
        raise ValueError("Hunting row exceeds one MiB or contains a null character")
    for name, value in row.items():
        if value is None:
            continue
        kind = contract.columns[name]
        valid = True
        if kind == "datetime":
            try:
                valid = (
                    isinstance(value, str)
                    and datetime.fromisoformat(
                        value.replace("Z", "+00:00"),
                    ).tzinfo
                    is not None
                )
            except ValueError:
                valid = False
        elif kind in {"string", "guid"}:
            valid = isinstance(value, str)
            if valid and kind == "guid":
                try:
                    UUID(value)
                except ValueError:
                    valid = False
        elif kind in {"long", "int"}:
            bits = 32 if kind == "int" else 64
            valid = type(value) is int and -(2 ** (bits - 1)) <= value < 2 ** (bits - 1)
        elif kind in {"real", "decimal"}:
            try:
                valid = type(value) in {int, float} and math.isfinite(value)
            except OverflowError:
                valid = False
        elif kind == "bool":
            valid = type(value) is bool
        elif kind != "dynamic":
            valid = False
        if not valid:
            raise ValueError("Invalid hunting type for " + name)
        if name in {"AccountId", "AccountObjectId", "DeviceId"} and (
            not isinstance(value, str) or len(value) > 512
        ):
            raise ValueError("Hunting identity exceeds the indexed identity bound")
    return json.loads(encoded)
