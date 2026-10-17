from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest


@pytest.mark.parametrize("bulk_error", [False, True])
async def test_cycle_services_general_work_before_bounded_batch(monkeypatch, bulk_error):
    from mockazure.evidence import bulk
    from mockazure.jobs import worker

    events = []

    async def ordinary(*args, **kwargs):
        events.append("ordinary")
        return True

    async def batch(*args, **kwargs):
        events.append("batch")
        if bulk_error:
            raise ValueError("malformed historical job")
        return 10

    monkeypatch.setattr(worker, "run_one", ordinary)
    monkeypatch.setattr(bulk, "run_historical_batch", batch)
    assert await worker.run_cycle(AsyncMock(), owner=uuid4(), now=datetime.now(UTC))
    assert events == ["ordinary", "batch"]
