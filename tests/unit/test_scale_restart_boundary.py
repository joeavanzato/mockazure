"""Fast evidence readers must wait for slow directory readers at the restart."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest


def test_all_first_pages_are_captured_before_any_continuation_resumes():
    from tools.benchmark_release import RestartBoundary

    report = {"api_restart": False}
    delayed_users = Event()
    evidence_arrived = [Event(), Event()]
    evidence_resumed = [Event(), Event()]
    restart_calls = []
    checkpoint_calls = []

    def restart():
        assert all(item.is_set() for item in evidence_arrived)
        assert not any(item.is_set() for item in evidence_resumed)
        restart_calls.append(True)

    boundary = RestartBoundary(report, restart, lambda: checkpoint_calls.append(True), timeout=3)

    def reader(family, caller):
        if family == "user":
            assert delayed_users.wait(2)
        else:
            evidence_arrived[caller].set()
        result = boundary.capture(family, caller, "/continuation")
        if family == "signIn":
            evidence_resumed[caller].set()
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(reader, family, caller)
            for family in ("signIn", "user")
            for caller in (0, 1)
        ]
        assert all(item.wait(2) for item in evidence_arrived)
        assert not any(item.is_set() for item in evidence_resumed)
        delayed_users.set()
        results = [future.result(timeout=3) for future in futures]
    assert restart_calls == [True] and checkpoint_calls == [True]
    assert report["api_restart"] is True
    span = report["restart_boundary"]
    for result in results:
        assert result["captured_ns"] <= span["started_ns"] <= span["completed_ns"]
        assert span["completed_ns"] <= result["resumed_ns"]


def test_missing_continuation_cannot_claim_restart_coverage():
    from tools.benchmark_release import RestartBoundary

    report = {"api_restart": False}
    boundary = RestartBoundary(report, lambda: None, lambda: None, timeout=0.05)
    with pytest.raises(AssertionError):
        boundary.capture("signIn", 0, None)
    assert report["api_restart"] is False


def test_real_readers_resume_all_four_continuations_after_restart(monkeypatch):
    import httpx

    from tools import benchmark_release as runner
    from tools.qualification_profile import QualificationProfile
    from tools.scale_dataset import iter_directory_batches, iter_evidence_batches

    seed = 61001
    rows = {
        "user": next(iter_directory_batches(seed, 100))["users"],
        "signIn": [
            item["payload"] for item in next(iter_evidence_batches(seed, 100, 1000))["records"]
        ],
    }
    first_pages, continuations = set(), set()
    from threading import Lock

    lock = Lock()
    ready_evidence = [Event(), Event()]
    release_users = Event()
    report = {"seed": seed, "users": 100, "records": 1000, "api_restart": False}

    class Client:
        def __init__(self):
            self.page = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, path, *, params, headers):
            family = "signIn" if "signIns" in path else "user"
            caller = int(headers["Authorization"].rsplit("-", 1)[1])
            key = family, caller
            if self.page == 0:
                if family == "user":
                    assert release_users.wait(3)
                else:
                    ready_evidence[caller].set()
                with lock:
                    first_pages.add(key)
            else:
                assert report["api_restart"] is True
                with lock:
                    continuations.add(key)
            half = len(rows[family]) // 2
            body = {"value": rows[family][self.page * half : (self.page + 1) * half]}
            if self.page == 0:
                body["@odata.nextLink"] = path + "?page=2"
            self.page += 1
            return httpx.Response(200, json=body)

    runtime = object.__new__(runner.Runtime)
    runtime.profile = QualificationProfile("m5", release=True)
    monkeypatch.setattr(runtime, "client", Client)

    def restart():
        assert len(first_pages) == 4 and not continuations

    boundary = runner.RestartBoundary(report, restart, lambda: None, timeout=4)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(runtime.read, family, caller, f"local-{caller}", report, boundary)
            for family in ("signIn", "user")
            for caller in (0, 1)
        ]
        assert all(item.wait(3) for item in ready_evidence)
        assert not continuations
        release_users.set()
        results = [future.result(timeout=4) for future in futures]
    assert len(continuations) == 4
    assert all(result["pages"] == 2 and result["restart"]["pages_after"] == 1 for result in results)
