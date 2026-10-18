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
        "activity": "c5393580-f805-4401-95e8-94b7a6ef2fc2",
        "graph": "00000003-0000-0000-c000-000000000000",
        "exchange": "00000002-0000-0ff1-ce00-000000000000",
        "arm": "797f4846-ba00-4fd7-ba43-dac1f8f63013",
    }
    try:
        return UUID(identifiers[resource])
    except KeyError:
        raise ValueError("Unsupported resource") from None


def resource_permissions(resource):
    if resource == "graph":
        return permission_catalog()["permissions"]
    if resource == "activity":
        return [
            {"name": name, "kind": "application", "id": identifier, "adminConsentRequired": True}
            for name, identifier in [
                ("ActivityFeed.Read", "594c1fb6-4f81-4475-ae41-0c394909246c"),
                ("ActivityFeed.ReadDlp", "4807a72c-ad38-4250-94c9-4eabfe26cd55"),
            ]
        ]
    if resource == "exchange":
        # Official Exchange managed-identity documentation, cited in the execution record.
        return [
            {
                "name": "Exchange.ManageAsApp",
                "kind": "application",
                "id": "dc50a0fb-09a3-484d-be87-e023b12c6440",
                "adminConsentRequired": True,
            }
        ]
    return []
