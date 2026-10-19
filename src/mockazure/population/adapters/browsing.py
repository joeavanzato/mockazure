"""Explicit safe projections. No table or column names come from a caller."""

from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor


def browser(
    key,
    service,
    path,
    row,
    identifier,
    version,
    fields,
    *,
    parent=None,
    conditions=(),
    payload=None,
    refs=None,
    limitations=None,
):
    def unsupported(_):
        raise ValueError("This family uses its declared service provisioning operations")

    return PayloadAdapter(
        FamilyDescriptor(
            key=key,
            service=service,
            api_path=path,
            display_fields=list(fields),
            contract_refs=refs or ["COVERAGE.md"],
            limitations=limitations or ["Metadata browser; use the public service API for writes."],
        ),
        ReadSpec(
            row,
            identifier,
            payload if payload is not None else json_object(**fields),
            version,
            conditions,
            parent,
        ),
        unsupported,
        None,
    )
