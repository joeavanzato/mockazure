import pytest

from tools import verify_graph_workflows


def test_callback_uses_shared_private_network_instead_of_container_loopback():
    assert (
        verify_graph_workflows.callback_address(
            {
                "lab_internal": {"IPAddress": "172.28.0.3"},
                "lab_ingress": {"IPAddress": "172.29.0.3"},
            }
        )
        == "172.28.0.3"
    )


@pytest.mark.parametrize(
    "networks",
    [
        {},
        {"lab_ingress": {"IPAddress": "172.29.0.3"}},
        {"lab_internal": {"IPAddress": "127.0.0.1"}},
        {"lab_internal": {"IPAddress": "8.8.8.8"}},
        {"lab_internal": {"IPAddress": "0.0.0.0"}},
        {
            "first_internal": {"IPAddress": "172.28.0.2"},
            "second_internal": {"IPAddress": "172.28.1.2"},
        },
    ],
)
def test_callback_network_must_be_unambiguous_and_private(networks):
    with pytest.raises(ValueError):
        verify_graph_workflows.callback_address(networks)


def test_restart_readiness_opens_a_fresh_verified_connection_each_attempt(monkeypatch):
    from types import SimpleNamespace

    from requests.exceptions import ConnectionError

    sessions = []

    class Session:
        def __init__(self, origin, ca):
            assert (origin, ca) == ("https://localhost:8443", "lab-ca.pem")
            self.closed = False
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def get(self, url, **kwargs):
            assert url == "https://localhost:8443/_mock/ready"
            assert kwargs == {"timeout": 5, "allow_redirects": False}
            if len(sessions) == 1:
                raise ConnectionError("backend not yet accepting new connections")
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(verify_graph_workflows, "LocalSession", Session)
    monkeypatch.setattr(verify_graph_workflows.time, "sleep", lambda _: None)
    verify_graph_workflows.wait_for_fresh_connection("https://localhost:8443", "lab-ca.pem")
    assert len(sessions) == 2
    assert all(session.closed for session in sessions)
