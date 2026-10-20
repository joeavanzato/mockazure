"""Offline source-verified catalog for the twenty approved hunting tables."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict

DATA = Path(__file__).parent / "data"


class HuntingTableContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    columns: dict[str, str]
    column_metadata: dict[str, dict]
    availability: str
    source_refs: list[str]
    entitlements: list[str]
    native_mappings: list[str]
    nullable_omissions: bool
    generated_columns: list[str]


@lru_cache(maxsize=1)
def sources():
    return json.loads((DATA / "sources.json").read_text("utf-8"))


@lru_cache(maxsize=1)
def catalog():
    return {
        key: HuntingTableContract.model_validate(value)
        for key, value in json.loads((DATA / "tables.json").read_text("utf-8")).items()
    }


def table_contract(name):
    try:
        return catalog()[name]
    except KeyError:
        raise ValueError("Unknown local hunting table") from None


def verify_sources():
    for name, source in sources().items():
        expected = (
            "https://learn.microsoft.com/en-us/defender-xdr/advanced-hunting-"
            + name.lower()
            + "-table"
        )
        if (
            source["url"] != expected
            or source["path"] != "sources/" + name + ".html"
            or not re.fullmatch("[0-9a-f]{40}", source["revision"])
            or hashlib.sha256((DATA / source["path"]).read_bytes()).hexdigest() != source["sha256"]
        ):
            raise ValueError("Hunting source integrity failure")
    for name, contract in catalog().items():
        if name != contract.name or any(ref not in sources() for ref in contract.source_refs):
            raise ValueError("Invalid hunting source reference")
    return {"tables": len(catalog()), "sources": len(sources())}
