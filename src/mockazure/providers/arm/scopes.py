"""Canonical ARM scope keys; authorization compares whole path segments."""

import re
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ArmScope:
    key: str
    subscription_id: UUID
    group: str | None
    resource_parts: tuple[str, ...]
    display_id: str
    parent_keys: tuple[str, ...]

    def contains(self, other: "ArmScope") -> bool:
        return self.key == other.key or self.key in other.parent_keys


def parse_scope(value: str) -> ArmScope:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2048
        or not value.startswith("/")
        or any(c in value for c in "\\%?#")
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("Invalid ARM scope")
    parts = value[1:].split("/")
    if any(not p or p in {".", ".."} or p != p.strip() for p in parts):
        raise ValueError("Invalid ARM scope segment")
    if len(parts) < 2 or parts[0].lower() != "subscriptions":
        raise ValueError("A subscription scope is required")
    if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", parts[1]):
        raise ValueError("Invalid subscription UUID")
    subscription = UUID(parts[1])
    parts[0], parts[1] = "subscriptions", str(subscription)
    parents = []
    group = None
    if len(parts) > 2:
        if len(parts) < 4 or parts[2].lower() != "resourcegroups":
            raise ValueError("Resource group scope is required")
        if len(parts[3]) > 90 or parts[3].endswith(".") or not re.fullmatch(r"[-\w.()]+", parts[3]):
            raise ValueError("Invalid resource group name")
        parts[2], group = "resourceGroups", parts[3]
        parents.append("/" + "/".join(parts[:2]).lower())
    if len(parts) > 4:
        if len(parts) < 8 or parts[4].lower() != "providers":
            raise ValueError("Resource provider scope is required")
        parts[4] = "providers"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+", parts[5]):
            raise ValueError("Invalid provider namespace")
        if (len(parts) - 6) % 2:
            raise ValueError("Resource types require paired names")
        for index in range(6, len(parts), 2):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", parts[index]):
                raise ValueError("Invalid resource type")
            if len(parts[index + 1]) > 256:
                raise ValueError("Invalid resource name")
            parents.append("/" + "/".join(parts[:index]).lower())
    display = "/" + "/".join(parts)
    # The first resource's parent is its group, never the provider namespace.
    if len(parts) > 4:
        parents[1] = "/" + "/".join(parts[:4]).lower()
    return ArmScope(display.lower(), subscription, group, tuple(parts[4:]), display, tuple(parents))
