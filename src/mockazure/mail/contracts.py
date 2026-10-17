"""Pinned Graph structural validation plus bounded write-operation contracts."""

import copy
import json
from functools import lru_cache
from pathlib import Path

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.providers.graph.schema_reference import validate_payload


@lru_cache(maxsize=1)
def operation_contracts() -> dict:
    return json.loads((Path(__file__).parent / "data/operations.json").read_text("utf-8"))


def operation_requirement(name: str) -> OperationRequirement:
    row = operation_contracts()["operations"].get(name)
    if row is None:
        raise ValueError("Unsupported mail operation")
    return OperationRequirement(
        service=row["service"],
        application_permissions=frozenset(row["application_permissions"]),
        delegated_permissions=frozenset(row["delegated_permissions"]),
        access_rule=row["access_rule"],
    )


def validate_mail_payload(kind: str, body: dict, partial: bool = False) -> dict:
    if kind not in {"messageRule", "security.auditLogQuery", "security.auditLogRecord"}:
        raise ValueError("Unsupported mail payload type")
    if not isinstance(body, dict):
        raise ValueError("Mail payload must be an object")
    try:
        encoded = json.dumps(body, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise ValueError("Mail payload must contain bounded JSON values") from None
    if len(encoded) > 1024 * 1024:
        raise ValueError("Mail payload exceeds one MiB")
    value = copy.deepcopy(body)
    if kind == "messageRule":
        if {"id", "hasError", "isReadOnly"} & value.keys():
            raise ValueError("Rule contains a read-only property")
        if not partial and {"actions", "displayName", "sequence"} - value.keys():
            raise ValueError("Rule requires actions, displayName and sequence")
        if "sequence" in value and (type(value["sequence"]) is not int or value["sequence"] < 0):
            raise ValueError("Rule sequence must be a nonnegative integer")
        if "displayName" in value and (
            not isinstance(value["displayName"], str) or not value["displayName"].strip()
        ):
            raise ValueError("Rule displayName must be nonempty")
        if "actions" in value and (not isinstance(value["actions"], dict) or not value["actions"]):
            raise ValueError("Rule actions must be a nonempty object")
    validate_payload("v1.0", kind, value, partial=(partial or kind == "messageRule"))
    return value
