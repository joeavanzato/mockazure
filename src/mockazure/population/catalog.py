"""Curated service capabilities; public support is never inferred from SQL tables."""

from functools import lru_cache

from mockazure.population import identity
from mockazure.population.adapters import (
    activity,
    administration,
    arm,
    content,
    directory,
    evidence,
    keyvault,
    licensing,
    log_records,
    mail,
    mail_folders,
    monitor,
    permissions,
    relationships,
    security,
    site_access,
    storage,
    workloads,
)


@lru_cache(maxsize=1)
def registry():
    entries = [
        *directory.adapters(),
        *administration.adapters(),
        *identity.adapters(),
        *content.adapters(),
        *licensing.adapters(),
        *relationships.adapters(),
        *permissions.adapters(),
        *storage.adapters(),
        *keyvault.adapters(),
        *(a for a in monitor.adapters() if a.describe().key != "monitor.records"),
        *mail_folders.adapters(),
        *log_records.adapters(),
        *mail.adapters(),
        *security.adapters(),
        *site_access.adapters(),
        *workloads.adapters(),
        *evidence.adapters(),
        *activity.adapters(),
        *arm.adapters(),
    ]
    from mockazure.population.actions import attach_public_actions

    entries = attach_public_actions(entries)
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
