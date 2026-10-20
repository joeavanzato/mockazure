from datetime import UTC, datetime, timedelta

import pytest

from mockazure.providers.graph.errors import GraphError
from mockazure.security.pim.contracts import schedule_bounds


def test_duration_and_justification_policy():
    now = datetime.now(UTC)
    assert schedule_bounds(
        {"expiration": {"type": "afterDuration", "duration": "PT1H"}},
        now,
        maximum=timedelta(hours=8),
        expiration_required=True,
    ) == (now, now + timedelta(hours=1))
    for value in [
        {"expiration": {"type": "afterDuration", "duration": "PT0S"}},
        {"expiration": {"type": "afterDuration", "duration": "P1D"}},
        {"expiration": {"type": "noExpiration"}},
        {"recurrence": {}, "expiration": {"type": "afterDuration", "duration": "PT1H"}},
    ]:
        with pytest.raises(GraphError):
            schedule_bounds(value, now, maximum=timedelta(hours=8), expiration_required=True)
