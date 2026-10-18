"""Official v1.0 types and explicit SharePoint/drive relationship grammar."""

import json
import math

from mockazure.providers.graph.schema_reference import validate_payload

KINDS = frozenset(
    {
        "site",
        "list",
        "listItem",
        "fieldValueSet",
        "drive",
        "driveItem",
        "permission",
        "driveItemVersion",
    }
)
EDGES = frozenset(
    {
        ("site", "drives", "drive"),
        ("site", "lists", "list"),
        ("site", "permissions", "permission"),
        ("list", "items", "listItem"),
        ("listItem", "fields", "fieldValueSet"),
        ("drive", "root", "driveItem"),
        ("driveItem", "children", "driveItem"),
        ("driveItem", "permissions", "permission"),
        ("driveItem", "versions", "driveItemVersion"),
        ("user", "drives", "drive"),
        ("group", "drives", "drive"),
    }
)


def validate_site_drive_record(kind, payload):
    if kind not in KINDS:
        raise ValueError("Unsupported SharePoint/OneDrive resource kind")
    if kind != "fieldValueSet":
        validate_payload("v1.0", kind, payload)
        return
    # fieldValueSet alone is an open CSDL type. Other workload types stay closed.
    validate_payload(
        "v1.0", kind, {key: value for key, value in payload.items() if key in {"id", "@odata.type"}}
    )

    def dynamic(value, depth=0):
        if depth > 8:
            raise ValueError("List fields nesting exceeds8")
        if isinstance(value, dict):
            if len(value) > 256:
                raise ValueError("Too many list fields")
            for key, child in value.items():
                if (
                    not isinstance(key, str)
                    or "@" in key
                    or not 1 <= len(key) <= 256
                    or "\0" in key
                ):
                    raise ValueError("Invalid dynamic list field name")
                dynamic(child, depth + 1)
        elif isinstance(value, list):
            if len(value) > 1000:
                raise ValueError("List field array exceeds1000")
            for child in value:
                dynamic(child, depth + 1)
        elif isinstance(value, str):
            if len(value) > 16384 or "\0" in value:
                raise ValueError("Invalid list field text")
            value.encode("utf-8")
        elif value is None or type(value) is bool:
            pass
        elif type(value) is int:
            if not -(2**63) <= value < 2**63:
                raise ValueError("List field integer is out of range")
        elif type(value) is float:
            if not math.isfinite(value):
                raise ValueError("List field number must be finite")
        else:
            raise ValueError("List fields require JSON values")

    dynamic({key: value for key, value in payload.items() if key != "@odata.type"})
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
        raise ValueError("List fields exceed1MiB")
