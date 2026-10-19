"""Curated service capabilities; public support is never inferred from SQL tables."""

from functools import lru_cache

from mockazure.population import identity
from mockazure.population.adapters import (
    activity,
    arm,
    directory,
    evidence,
    keyvault,
    mail,
    monitor,
    security,
    storage,
    workloads,
)


@lru_cache(maxsize=1)
def registry():
    entries = [
        *directory.adapters(),
        *identity.adapters(),
        *storage.adapters(),
        *keyvault.adapters(),
        *monitor.adapters(),
        *mail.adapters(),
        *security.adapters(),
        *workloads.adapters(),
        *evidence.adapters(),
        *activity.adapters(),
        *arm.adapters(),
    ]
    result = {item.describe().key: item for item in entries}
    if len(result) != len(entries):
        raise RuntimeError("Duplicate population family")
    return result


def get_adapter(family):
    try:
        return registry()[family]
    except KeyError:
        raise ValueError("Unsupported resource family") from None


def list_families():
    return [adapter.describe() for _, adapter in sorted(registry().items())]
