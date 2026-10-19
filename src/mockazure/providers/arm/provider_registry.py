"""Explicit provider operation bindings; unknown paths never become inventory writes."""

import copy
import json
import re
from functools import lru_cache
from pathlib import Path

from mockazure.cloud.contracts import _catalog
from mockazure.providers.arm.errors import ArmError


def provider_profiles():
    return json.loads(
        (Path(__file__).with_name("data") / "providers.json").read_text(encoding="utf-8")
    )


def provider_name(namespace):
    name = next(
        (name for name in provider_profiles() if name.casefold() == namespace.casefold()), None
    )
    if name is None:
        raise ArmError(404, "InvalidResourceNamespace", "Provider namespace is not modeled")
    return name


@lru_cache
def operation_bindings():
    # Pinned contracts are immutable for a running server. Avoid reparsing all
    # source provenance and rebuilding route expressions on every ARM request.
    diagnostic = [
        row for row in _catalog("queries")["operations"] if "/diagnosticSettings" in row["path"]
    ]
    bindings = []
    for operation in [*_catalog("arm-providers")["operations"], *diagnostic]:
        parts = re.split(r"(\{[^}]+\})", operation["path"])
        pattern = "".join(
            ".+"
            if part == "{resourceUri}"
            else "[^/]+"
            if part.startswith("{")
            else re.escape(part)
            for part in parts
        )
        bindings.append((operation, re.compile(pattern, re.I)))
    return tuple(bindings)


def resolve_operation(method: str, resource_id: str, api_version: str) -> dict:
    if any(c in resource_id for c in "%?#\\") or "//" in resource_id:
        raise ArmError(400, "InvalidResourceId", "Invalid provider path")
    found = []
    for operation, pattern in operation_bindings():
        if operation["method"] == method.upper() and pattern.fullmatch(resource_id):
            found.append(operation)
    if not found:
        raise ArmError(
            501, "MockAzureOperationNotImplemented", "Provider operation is not implemented"
        )
    found.sort(key=lambda row: "{resourceUri}" in row["path"])
    match = next((row for row in found if row["version"] == api_version), None)
    if match is None:
        raise ArmError(400, "InvalidApiVersionParameter", "Unsupported provider operation version")
    return copy.deepcopy(match)


def provider_view(subscription, namespace, state):
    name = provider_name(namespace)
    profile = provider_profiles()[name]
    return {
        "id": f"/subscriptions/{subscription}/providers/{name}",
        "namespace": name,
        "registrationState": state,
        "registrationPolicy": "RegistrationRequired",
        "resourceTypes": [
            {
                key: value
                for key, value in row.items()
                if key not in {"schemaNamespace", "schemaNamespaces"}
            }
            for row in profile["resourceTypes"]
        ],
    }
