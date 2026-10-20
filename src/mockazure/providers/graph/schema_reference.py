"""Validate structural Graph types against the pinned Microsoft CSDL subset.

Operation permissions, supported fields and query behavior are separate contracts.
Open CSDL types are deliberately closed to declared fields for imported evidence.
"""

import base64
import binascii
import json
import math
import re
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from uuid import UUID


@lru_cache(maxsize=2)
def schema(version: str) -> dict:
    if version not in {"v1.0", "beta"}:
        raise ValueError("Unsupported Graph schema version")
    return json.loads((Path(__file__).parent / "schemas" / f"{version}.json").read_text("utf-8"))


def validate_payload(version: str, resource: str, payload: dict, *, partial: bool = False) -> None:
    types = schema(version)["types"]

    def dynamic(value, depth):
        if depth > 16:
            raise ValueError("Graph audit dictionary nesting limit exceeded")
        if value is None or type(value) is bool:
            return
        if type(value) is int:
            if not -(2**63) <= value < 2**63:
                raise ValueError("Graph audit integer out of range")
        elif type(value) is float:
            if not math.isfinite(value):
                raise ValueError("Graph audit dictionary requires finite numbers")
        elif isinstance(value, str):
            check("Edm.String", value, depth=depth)
        elif isinstance(value, list):
            if len(value) > 10000:
                raise ValueError("Graph audit collection limit exceeded")
            for item in value:
                dynamic(item, depth + 1)
        elif isinstance(value, dict):
            if len(value) > 10000:
                raise ValueError("Graph audit dictionary limit exceeded")
            for key, item in value.items():
                check("Edm.String", key, False, depth, 256)
                if key.startswith("@") or "@" in key:
                    raise ValueError("Dynamic audit annotations are unsupported")
                dynamic(item, depth + 1)
        else:
            raise ValueError("Graph audit dictionary requires JSON values")

    def check(kind, value, nullable=True, depth=0, max_chars=16384):
        if depth > 16:
            raise ValueError("Graph payload nesting limit exceeded")
        if value is None:
            if not nullable:
                raise ValueError("Graph nonnullable property is null")
            return
        if kind.startswith("Collection("):
            if not isinstance(value, list) or len(value) > 10000:
                raise ValueError("Graph collection required")
            for item in value:
                check(kind[11:-1], item, False, depth + 1)
            return
        if kind == "Edm.String":
            if not isinstance(value, str) or len(value) > max_chars or "\0" in value:
                raise ValueError("Graph string required")
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("Graph string must be valid Unicode") from None
        elif kind == "Edm.Binary":
            if not isinstance(value, str) or len(value) > max_chars:
                raise ValueError("Graph base64 string required")
            try:
                base64.b64decode(value, validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("Invalid Graph base64 string") from None
        elif kind == "Edm.Boolean":
            if type(value) is not bool:
                raise ValueError("Graph boolean required")
        elif kind in {"Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Byte"}:
            bits = {"Edm.Int16": 16, "Edm.Int32": 32, "Edm.Int64": 64, "Edm.Byte": 8}[kind]
            low, high = (
                (0, 255) if kind == "Edm.Byte" else (-(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
            )
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Graph integer out of range")
        elif kind in {"Edm.Double", "Edm.Single", "Edm.Decimal"}:
            if type(value) not in {int, float} or not math.isfinite(value):
                raise ValueError("Graph finite number required")
        elif kind == "Edm.Date":
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("Graph date requires YYYY-MM-DD")
            date.fromisoformat(value)
        elif kind == "Edm.DateTimeOffset":
            if not isinstance(value, str):
                raise ValueError("Graph timestamp required")
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError("Invalid Graph timestamp") from None
            if parsed.tzinfo is None or "T" not in value:
                raise ValueError("Graph timestamp requires offset")
        elif kind == "Edm.TimeOfDay":
            # The pinned Intune properties omit Precision (CSDL default: zero).
            if not isinstance(value, str) or not re.fullmatch(
                r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]", value
            ):
                raise ValueError("Graph time of day requires HH:mm:ss")
        elif kind == "Edm.Duration":
            if not isinstance(value, str) or len(value) > 128 or not re.fullmatch(
                r"-?P(?=\d|T\d)(?:\d+D)?(?:T(?=\d)(?:\d+H)?(?:\d+M)?(?:\d+(?:\.\d+)?S)?)?",
                value,
            ):
                raise ValueError("Graph duration requires an ISO 8601 day/time duration")
        elif kind == "Edm.Guid":
            if not isinstance(value, str):
                raise ValueError("Graph GUID required")
            UUID(value)
        elif kind.startswith("Edm."):
            raise ValueError("Unsupported Graph primitive")
        else:
            name = kind.removeprefix("graph.").removeprefix("microsoft.graph.")
            contract = types.get(name)
            if contract is None:
                raise ValueError("Unknown Graph schema type")
            if "enum" in contract:
                if not isinstance(value, str) or value not in contract["enum"]:
                    raise ValueError("Invalid Graph enum")
                return
            if not isinstance(value, dict):
                raise ValueError("Graph object required")
            annotation = value.get("@odata.type")
            if annotation is not None and annotation != f"#microsoft.graph.{name}":
                if not isinstance(annotation, str) or not annotation.startswith(
                    "#microsoft.graph."
                ):
                    raise ValueError("Graph type annotation mismatch")
                derived = annotation.removeprefix("#microsoft.graph.")
                ancestor = derived
                while ancestor in types and ancestor != name:
                    ancestor = types[ancestor].get("base")
                if ancestor != name:
                    raise ValueError("Graph type annotation mismatch")
                name, contract = derived, types[derived]
            fields = contract["properties"]
            extra = value.keys() - fields.keys() - {"@odata.type"}
            if extra:
                if name not in {"security.auditRecordTypeDictionary", "security.defaultAuditData"}:
                    raise ValueError("Undeclared Graph property")
                dynamic({key: value[key] for key in extra}, depth + 1)
            if "@odata.type" in value and value["@odata.type"] != f"#microsoft.graph.{name}":
                raise ValueError("Graph type annotation mismatch")
            if not partial and set(contract["required"]) - value.keys():
                raise ValueError("Missing required Graph property")
            for field, item in value.items():
                if field == "@odata.type" or field in extra:
                    continue
                prop = fields[field]
                limit = (
                    4 * 1024 * 1024
                    if name == "modifiedProperty" and field in {"oldValue", "newValue"}
                    else 16384
                )
                check(prop["type"], item, prop["nullable"], depth + 1, limit)

    check(resource, payload, False)
