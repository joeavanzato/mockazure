"""Stable client-assigned identifiers scoped to tenant and dataset."""

import json
from uuid import UUID, uuid5

NAMESPACE = UUID("ccce61ce-0281-5f10-adb8-6c96afdc06a7")


def stable_id(tenant_id: UUID, namespace: str, logical_key: str, seed: int) -> UUID:
    return uuid5(NAMESPACE, json.dumps([str(tenant_id), namespace, logical_key, seed]))
