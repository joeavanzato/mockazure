"""Strict service profile validated directly against the cached Microsoft definitions."""

import json
import re
from functools import lru_cache

from mockazure.cloud.contracts import DATA, load_contract
from mockazure.providers.keyvault.errors import VaultError


@lru_cache
def definitions(api_version="2025-07-01"):
    operation = "v7.SetSecret" if api_version == "7.0" else "SetSecret"
    source = load_contract("keyvault", operation)["sources"][0]["localPath"]
    raw = json.loads((DATA / "keyvault" / source).read_text(encoding="utf-8"))["definitions"]

    def expand(schema, depth=0):
        if depth > 16:
            raise RuntimeError("Recursive schema inheritance")
        result = dict(schema)
        parents = result.pop("allOf", [])
        if parents:
            fields = {}
            required = []
            for parent in parents:
                inherited = expand(raw[parent["$ref"].split("/")[-1]], depth + 1)
                fields.update(inherited.get("properties", {}))
                required.extend(inherited.get("required", []))
            result["properties"] = fields | result.get("properties", {})
            result["required"] = list(dict.fromkeys(required + result.get("required", [])))
        if "properties" in result:
            result.setdefault("type", "object")
        return result

    return {name: expand(schema) for name, schema in raw.items()}


def validate_response(schema, value, api_version):
    if api_version == "7.0":
        rows = (
            value["value"] if schema in {"SecretListResult", "DeletedSecretListResult"} else [value]
        )
        for row in rows:
            if isinstance(row, dict) and "attributes" in row:
                row["attributes"].pop("recoverableDays", None)
    validate_shape(schema, value, api_version=api_version)


def bad(message="Invalid secret request"):
    raise VaultError(400, "BadParameter", message)


def name_key(name):
    if not isinstance(name, str) or not re.fullmatch(r"[0-9a-zA-Z-]{1,127}", name):
        bad("Invalid secret name")
    return name.lower()


def validate_shape(schema, value, *, writable=False, depth=0, api_version="2025-07-01"):
    if depth > 12:
        bad("Request nesting exceeds local limit")
    if isinstance(schema, str):
        schema = definitions(api_version)[schema]
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/definitions/"):
            raise RuntimeError("Unsupported schema reference")
        return validate_shape(
            definitions(api_version)[ref.split("/")[-1]],
            value,
            writable=writable,
            depth=depth + 1,
            api_version=api_version,
        )
    if writable and schema.get("readOnly"):
        bad("Read-only field is not writable")
    if "enum" in schema and value not in schema["enum"]:
        bad("Invalid enumeration")
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            bad("Expected object")
        fields = schema.get("properties", {})
        if any(k not in value for k in schema.get("required", [])):
            bad("Missing required field")
        for key, item in value.items():
            validate_shape({"type": "string"}, key, depth=depth + 1, api_version=api_version)
            if key not in fields:
                extra = schema.get("additionalProperties", False)
                if not isinstance(extra, dict):
                    bad("Unknown field")
                validate_shape(
                    extra, item, writable=writable, depth=depth + 1, api_version=api_version
                )
            else:
                if writable and fields[key].get("readOnly"):
                    bad("Read-only field is not writable")
                validate_shape(
                    fields[key], item, writable=writable, depth=depth + 1, api_version=api_version
                )
    elif kind == "array":
        if not isinstance(value, list):
            bad("Expected array")
        for item in value:
            validate_shape(
                schema["items"], item, writable=writable, depth=depth + 1, api_version=api_version
            )
    elif kind == "string":
        if not isinstance(value, str):
            bad("Expected string")
        try:
            value.encode("utf-8")
        except UnicodeError:
            bad("String must contain valid Unicode")
    elif kind == "integer":
        if type(value) is not int or not -(2**63) <= value < 2**63:
            bad("Expected 64-bit integer")
    elif kind == "boolean":
        if type(value) is not bool:
            bad("Expected boolean")
    elif kind is not None:
        raise RuntimeError("Unsupported schema type")


def checked_body(body, operation):
    validate_shape(
        "SecretSetParameters" if operation == "set" else "SecretUpdateParameters",
        body,
        writable=True,
    )
    if "value" in body and len(body["value"].encode("utf-8")) > 25600:
        bad("Secret exceeds 25 KiB")
    if "contentType" in body and len(body["contentType"]) > 255:
        bad("Content type exceeds 255 characters")
    tags = body.get("tags", {})
    if len(tags) > 15 or any(len(k) > 512 or len(v) > 512 for k, v in tags.items()):
        bad("Secret tags exceed limits")
    return body
