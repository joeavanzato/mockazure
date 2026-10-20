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


def parse_scope(
    value: str, *, allow_subscription_resource=False, allow_provider_registration=False
) -> ArmScope:
    if (
        not isinstance(value, str)
        or not 1 <= len(value.encode("utf-8")) <= 2048
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
    provider_start = 2
    if len(parts) > 2 and parts[2].lower() == "resourcegroups":
        if len(parts) < 4:
            raise ValueError("Resource group scope is required")
        if len(parts[3]) > 90 or parts[3].endswith(".") or not re.fullmatch(r"[-\w.()]+", parts[3]):
            raise ValueError("Invalid resource group name")
        parts[2], group, provider_start = "resourceGroups", parts[3], 4
        parents.append("/" + "/".join(parts[:2]).lower())
    elif len(parts) > 2 and not allow_subscription_resource:
        raise ValueError("Resource group scope is required")
    if len(parts) > provider_start:
        if (
            allow_subscription_resource
            and allow_provider_registration
            and group is None
            and len(parts) == 4
            and parts[2].lower() == "providers"
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+", parts[3])
        ):
            parts[2] = "providers"
            display = "/" + "/".join(parts)
            return ArmScope(
                display.lower(),
                subscription,
                None,
                tuple(parts[2:]),
                display,
                ("/" + "/".join(parts[:2]).lower(),),
            )
        if len(parts) < provider_start + 4 or parts[provider_start].lower() != "providers":
            raise ValueError("Resource provider scope is required")
        parts[provider_start] = "providers"
        namespace = r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+"
        if not re.fullmatch(namespace, parts[provider_start + 1]):
            raise ValueError("Invalid provider namespace")
        if (len(parts) - provider_start - 2) % 2:
            raise ValueError("Resource types require paired names")
        for index in range(provider_start + 2, len(parts), 2):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", parts[index]):
                raise ValueError("Invalid resource type")
            if len(parts[index + 1]) > 256:
                raise ValueError("Invalid resource name")
            end = provider_start if index == provider_start + 2 else index
            parents.append("/" + "/".join(parts[:end]).lower())
    display = "/" + "/".join(parts)
    return ArmScope(
        display.lower(), subscription, group, tuple(parts[provider_start:]), display, tuple(parents)
    )
