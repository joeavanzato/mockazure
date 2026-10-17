from types import SimpleNamespace

import pytest


def test_readiness_reissues_snapshot_until_all_expected_ids_are_visible(monkeypatch):
    from tools import mail_runtime

    calls = []

    class Client:
        def post(self, url, **kwargs):
            calls.append(("post", url))
            return SimpleNamespace(
                content=b"json", status_code=201, json=lambda: {"id": str(len(calls))}
            )

        def get(self, url, **kwargs):
            # First immutable snapshot is partial; only a new query can see publication.
            count = sum(kind == "post" for kind, _ in calls)
            calls.append(("get", url))
            values = [{"id": "a"}] if count == 1 else [{"id": "a"}, {"id": "b"}]
            return SimpleNamespace(content=b"json", status_code=200, json=lambda: {"value": values})

    monkeypatch.setattr(mail_runtime, "await_query", lambda *args: None)
    monkeypatch.setattr(mail_runtime.time, "sleep", lambda seconds: None)
    mail_runtime.await_fixture_publication(Client(), "https://localhost:34444", {}, {}, {"a", "b"})
    assert sum(kind == "post" for kind, _ in calls) == 2


def test_readiness_rejects_wrong_ids_instead_of_accepting_count(monkeypatch):
    from tools import mail_runtime

    class Client:
        def post(self, url, **kwargs):
            return SimpleNamespace(content=b"json", status_code=201, json=lambda: {"id": "query"})

        def get(self, url, **kwargs):
            return SimpleNamespace(
                content=b"json", status_code=200, json=lambda: {"value": [{"id": "wrong"}]}
            )

    monkeypatch.setattr(mail_runtime, "await_query", lambda *args: None)
    with pytest.raises(AssertionError, match="Unexpected audit fixture identity"):
        mail_runtime.await_fixture_publication(Client(), "https://localhost:34444", {}, {}, {"a"})
