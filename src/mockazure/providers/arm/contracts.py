"""Offline validation against pinned official ARM Swagger definitions."""

import copy
import hashlib
import json
import math
import posixpath
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name("data")


@lru_cache
def manifest():
    return json.loads((DATA / "references.json").read_text(encoding="utf-8"))


@lru_cache
def document(source):
    entry = manifest()["files"][source]
    return json.loads((DATA / entry["path"]).read_text(encoding="utf-8"))


def verify_references():
    entries = manifest()["files"]
    for entry in entries.values():
        path = (DATA / entry["path"]).resolve()
        if not path.is_relative_to(DATA.resolve()):
            raise ValueError("Reference path escaped schema directory")
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("Pinned ARM schema bytes changed")
    return len(entries)


def builtin_roles():
    return json.loads((DATA / "roles.json").read_text(encoding="utf-8"))["roles"]


def operation_contract(name, version):
    operations = json.loads((DATA / "operations.json").read_text(encoding="utf-8"))
    value = operations.get(name)
    if not value or version not in value["versions"]:
        raise ValueError("Unsupported ARM operation or API version")
    return copy.deepcopy(value)


def resolve(schema, source):
    if "$ref" not in schema:
        return schema, source
    file, _, fragment = schema["$ref"].partition("#")
    if file:
        source = posixpath.normpath(posixpath.join(posixpath.dirname(source), file))
    value = document(source)
    for part in fragment.lstrip("/").split("/") if fragment else []:
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return resolve(value, source)


def object_shape(schema, source):
    schema, source = resolve(schema, source)
    properties, required, additional = {}, set(schema.get("required", [])), None
    for parent in schema.get("allOf", []):
        p, r, a = object_shape(parent, source)
        properties.update(p)
        required.update(r)
        if a is not None:
            additional = a
    properties.update({k: (v, source) for k, v in schema.get("properties", {}).items()})
    if "additionalProperties" in schema:
        additional = (schema["additionalProperties"], source)
    return properties, required, additional


def check(value, schema, source, *, partial=False, depth=0):
    if depth > 32:
        raise ValueError("ARM payload nesting is too deep")
    schema, source = resolve(schema, source)
    if value is None and schema.get("x-nullable"):
        return
    kind = schema.get("type")
    if kind is None and ("properties" in schema or "allOf" in schema):
        kind = "object"
    if "enum" in schema and not schema.get("x-ms-enum", {}).get("modelAsString", False):
        if value not in schema["enum"]:
            raise ValueError("Invalid ARM enumeration")
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError("ARM object required")
        props, required, additional = object_shape(schema, source)
        if not partial and not required <= value.keys():
            raise ValueError("Missing required ARM field")
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("ARM property names must be strings")
            if key in props:
                node, origin = props[key]
                check(item, node, origin, depth=depth + 1)
            elif additional is not None:
                node, origin = additional
                if node is False:
                    raise ValueError("Unknown ARM property")
                if isinstance(node, dict):
                    check(item, node, origin, depth=depth + 1)
            elif props:
                raise ValueError("Unknown ARM property")
            else:
                # Arbitrary provider properties are still bounded JSON, checked
                # for serializability and size by validate_arm_payload.
                continue
    elif kind == "array":
        if not isinstance(value, list):
            raise ValueError("ARM array required")
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10000):
            raise ValueError("ARM array outside size bounds")
        for item in value:
            check(item, schema.get("items", {}), source, depth=depth + 1)
    elif kind == "string":
        if not isinstance(value, str):
            raise ValueError("ARM string required")
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 1048576):
            raise ValueError("ARM string outside length bounds")
        if pattern := schema.get("pattern"):
            if re.search(pattern, value) is None:
                raise ValueError("ARM string pattern mismatch")
        if schema.get("format") == "date-time":
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("ARM timestamp requires timezone")
    elif kind in {"integer", "number"}:
        if type(value) not in ({int} if kind == "integer" else {int, float}) or not math.isfinite(
            value
        ):
            raise ValueError("ARM numeric value required")
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ValueError("ARM number outside bounds")
        bits = {"int32": 32, "int64": 64}.get(schema.get("format"))
        if bits and not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            raise ValueError("ARM integer outside format bounds")
    elif kind == "boolean" and type(value) is not bool:
        raise ValueError("ARM boolean required")


def validate_arm_payload(type_name, payload, partial=False):
    if not isinstance(payload, dict):
        raise ValueError("ARM payload must be an object")
    try:
        encoded = json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("ARM payload must contain finite JSON data") from None
    if len(encoded.encode()) > 2 * 1024 * 1024:
        raise ValueError("ARM payload exceeds local limit")
    for alias in ("resources", "subscriptions", "assignments", "definitions", "activity"):
        source = manifest()["roots"][alias]
        definitions = document(source).get("definitions", {})
        if type_name in definitions:
            check(payload, definitions[type_name], source, partial=partial)
            return copy.deepcopy(payload)
    raise ValueError("Unknown ARM schema type")
