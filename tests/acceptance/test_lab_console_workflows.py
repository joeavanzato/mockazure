import copy
import json
import os
from pathlib import Path

import pytest

from mockazure.population.catalog import list_families
from tools.verify_lab_console import (
    REQUIRED_PROFILES,
    expected_steps,
    source_fingerprint,
    validate_report,
)


def valid_report():
    return {
        "schema_version": 1,
        "source_sha": "a" * 40,
        "source_fingerprint": source_fingerprint(),
        "distribution": {"kind": "native", "mockazure_version": "test"},
        "dependencies": {"python": "3.12"},
        "profiles": {
            name: {
                "digest": "b" * 64,
                "state": "succeeded",
                "run_id": "run",
                "replay_state": "succeeded",
                "replay_created": 0,
                "steps": [
                    {
                        "case_id": case_id,
                        "family": step["family"],
                        "state": "succeeded",
                        "count": step["count"],
                        "offset": step["count"],
                    }
                    for case_id, step in expected_steps(name).items()
                ],
                "public_counts": {
                    "users": 10000 if name == "volume-lab" else 20,
                    "signins": 100000
                    if name == "volume-lab"
                    else (120 if name == "investigation-lab" else 100),
                },
                "seconds": 1,
                "interrupted_resumed": name == "volume-lab",
            }
            for name in REQUIRED_PROFILES
        },
        "catalog": {
            f.key: {
                "browse_case": "browse:" + f.key,
                "state": "passed",
                "operations": [o.key for o in f.operations],
            }
            for f in list_families()
        },
        "unsupported_case": {"case_id": "unsupported-family", "status": 404},
        "failed_cases": [],
        "limitations": ["Synthetic only"],
    }


def test_report_rejects_missing_or_corrupt_evidence():
    original = valid_report()
    validate_report(original, expected_fingerprint=source_fingerprint())
    mutations = [
        lambda r: r["profiles"]["developer-lab"]["steps"][0].update(
            case_id="developer-lab:invented"
        ),
        lambda r: r["profiles"].pop("collection-lab"),
        lambda r: r["catalog"].pop(next(iter(r["catalog"]))),
        lambda r: r["profiles"]["volume-lab"]["public_counts"].update(signins=99),
        lambda r: r["profiles"]["collection-lab"]["public_counts"].update(signins=0),
        lambda r: r["profiles"]["developer-lab"].update(replay_created=1),
        lambda r: r.update(source_fingerprint="0" * 64),
        lambda r: r["profiles"]["developer-lab"]["steps"][0].update(offset=0),
        lambda r: r["failed_cases"].append("failed"),
    ]
    for mutate in mutations:
        value = copy.deepcopy(original)
        mutate(value)
        with pytest.raises(ValueError):
            validate_report(value, expected_fingerprint=source_fingerprint())


def test_actual_local_workflow_report():
    path = os.environ.get("MOCKAZURE_LAB_REPORT")
    if not path:
        pytest.skip("Set MOCKAZURE_LAB_REPORT to the generated local qualification report")
    validate_report(json.loads(Path(path).read_text()), expected_fingerprint=source_fingerprint())


@pytest.mark.parametrize("code", ["PreviewStale", "TenantBusy"])
async def test_qualification_refreshes_only_explicitly_stale_previews(code):
    from types import SimpleNamespace

    import httpx

    from tools.verify_lab_console import submit_preview

    calls = []

    async def route(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/previews"):
            return httpx.Response(201, json={"id": str(len(calls)), "digest": "checked"})
        if len(calls) == 2:
            return httpx.Response(409, json={"error": {"code": code}})
        return httpx.Response(201, json={"id": "run"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(route), base_url="https://localhost:8443"
    ) as http:

        async def request(method, path, **options):
            return (await http.request(method, path, **options)).json()

        client = SimpleNamespace(http=http, request=request)
        if code == "PreviewStale":
            preview, created, retries = await submit_preview(
                client, {"target": "disposable"}, delay=0
            )
            assert preview["id"] == "3" and created["id"] == "run" and retries == 1
            assert len(calls) == 4
        else:
            with pytest.raises(RuntimeError, match="TenantBusy"):
                await submit_preview(client, {"target": "disposable"}, delay=0)
            assert len(calls) == 2
