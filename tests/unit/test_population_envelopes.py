from uuid import uuid4

import pytest

from mockazure.population.catalog import get_adapter
from mockazure.population.types import Mutation


def test_evidence_envelope_cannot_override_catalog_family_or_api_version():
    identifier = str(uuid4())
    body = {
        "id": identifier,
        "occurred_at": "2026-10-01T00:00:00Z",
        "available_at": "2026-10-01T00:00:00Z",
        "payload": {
            "id": identifier,
            "createdDateTime": "2026-10-01T00:00:00Z",
            "userId": str(uuid4()),
        },
    }
    adapter = get_adapter("evidence.signIn")
    for extra in ({"family": "signIn"}, {"api_version": "v1.0"}):
        with pytest.raises(ValueError, match="catalog"):
            adapter.validate(
                Mutation(operation="import", logical_key="event", payload=body | extra)
            )
