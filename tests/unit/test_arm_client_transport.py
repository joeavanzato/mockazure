import pytest
import requests

from integrations.python.arm import LocalSession


def test_local_sdk_transport_rechecks_prepared_redirect_sends(monkeypatch):
    sent = []
    monkeypatch.setattr(
        requests.Session, "send", lambda self, request, **kwargs: sent.append(request.url)
    )
    session = LocalSession("https://localhost:36443", "unused-local-ca")
    local = requests.Request("GET", "https://localhost:36443/subscriptions").prepare()
    session.send(local)
    assert sent == [local.url]
    for uri in (
        "https://example.com/redirect",
        "https://localhost:36444/other",
        "http://localhost:36443/cleartext",
        "https://localhost:36443/path#fragment",
    ):
        request = requests.Request("GET", uri).prepare()
        with pytest.raises(ValueError, match="escaped"):
            session.send(request)
    assert sent == [local.url]
