import json
from unittest.mock import Mock

import pytest

from tools.benchmark_coverage import wait_for_replicas


def test_restart_requires_both_current_replicas_to_be_healthy(monkeypatch):
    states = iter(
        [
            [
                {"State": {"Running": True, "Health": {"Status": "healthy"}}},
                {"State": {"Running": True, "Health": {"Status": "starting"}}},
            ],
            [
                {"State": {"Running": True, "Health": {"Status": "healthy"}}},
                {"State": {"Running": True, "Health": {"Status": "healthy"}}},
            ],
        ]
    )
    command = Mock(side_effect=lambda *args, **kwargs: json.dumps(next(states)))
    client = Mock()
    client.get.return_value.status_code = 200
    sleep = Mock()
    monkeypatch.setattr("tools.benchmark_coverage.time.sleep", sleep)
    wait_for_replicas(command, client, ["api-a", "api-b"], attempts=2)
    assert command.call_count == 2
    assert all(
        call.args[0] == ["docker", "inspect", "api-a", "api-b"] for call in command.call_args_list
    )
    assert client.get.call_count == 1
    sleep.assert_called_once()


def test_restart_does_not_release_readers_when_a_replica_never_recovers(monkeypatch):
    command = Mock(
        return_value=json.dumps(
            [
                {"State": {"Running": True, "Health": {"Status": "healthy"}}},
                {"State": {"Running": True, "Health": {"Status": "unhealthy"}}},
            ]
        )
    )
    client = Mock()
    monkeypatch.setattr("tools.benchmark_coverage.time.sleep", lambda delay: None)
    with pytest.raises(AssertionError, match="replicas"):
        wait_for_replicas(command, client, ["api-a", "api-b"], attempts=2)
    assert not client.get.called
