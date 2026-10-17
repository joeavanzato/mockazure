"""Official Graph permission identities; OAuth role names remain the token contract."""

import json
from functools import lru_cache
from pathlib import Path
from uuid import UUID


@lru_cache(maxsize=1)
def permission_catalog() -> dict:
    return json.loads((Path(__file__).parent / "data" / "permissions.json").read_text("utf-8"))


def permission_id(name: str, kind: str) -> UUID:
    for entry in permission_catalog()["permissions"]:
        if (entry["name"], entry["kind"]) == (name, kind):
            return UUID(entry["id"])
    raise ValueError("Unsupported Graph permission name or kind")


def permission_name(identifier: UUID, kind: str) -> str:
    for entry in permission_catalog()["permissions"]:
        if (UUID(entry["id"]), entry["kind"]) == (identifier, kind):
            return entry["name"]
    raise ValueError("Unsupported Graph permission identifier or kind")


def resource_app_id(resource: str) -> UUID:
    identifiers = {
        "graph": "00000003-0000-0000-c000-000000000000",
        "exchange": "00000002-0000-0ff1-ce00-000000000000",
        "arm": "797f4846-ba00-4fd7-ba43-dac1f8f63013",
    }
    try:
        return UUID(identifiers[resource])
    except KeyError:
        raise ValueError("Unsupported resource") from None
