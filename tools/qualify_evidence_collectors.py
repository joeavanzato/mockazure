"""Run pinned original collectors and verify actual serialized records."""

import json
import ssl
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import httpx
from evidence_collector_reports import sha, valid_denial, verify_outputs
from prepare_evidence_collectors import seed

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime/m3b"
STATE = RUNTIME / "clients/powershell-state"
CLIENTS = ["docker", "compose", "-f", "compose.clients.m3b.yaml"]


def host(path):
    if path.startswith("/state/"):
        return STATE / path.removeprefix("/state/")
    if path.startswith("/vendor/"):
        return ROOT / "work/vendor" / path.removeprefix("/vendor/")
    raise ValueError("Unexpected collector output or source path")


def validate_case(case, result, dataset):
    paths = [host(p) for p in result["files"]]
    sources = {str(host(p)): digest for p, digest in result["source_hashes"].items()}
    start, end = case["start"], case["end"]
    selected = []
    for record in dataset["records"]:
        p = record["payload"]
        if record["family"] != ("signIn" if "SignIn" in case["function"] else "directoryAudit"):
            continue
        stamp = p.get("createdDateTime", p.get("activityDateTime"))
        inside = start <= stamp <= end
        if record["family"] == "signIn":
            event = p["signInEventTypes"][0]
            if case["collector"] == "hawk":
                match = (
                    inside and event == "interactiveUser" and p["userPrincipalName"] == case["user"]
                )
            else:
                match = inside and (case["event_types"] == ["All"] or event in case["event_types"])
                if case.get("user"):
                    match = match and p["userPrincipalName"].startswith(case["user"])
        else:
            actor = p.get("initiatedBy", {}).get("user", {}).get("userPrincipalName", "")
            match = inside
            if case.get("user"):
                match = match and actor.startswith(case["user"])
                if case.get("all"):
                    match = match or any(
                        t.get("userPrincipalName") == case["user"] for t in p["targetResources"]
                    )
        if match:
            selected.append(p)
    expected = {
        p["id"]: {"ipAddress": p["ipAddress"]}
        if "ipAddress" in p
        else {"activityDisplayName": p["activityDisplayName"]}
        for p in selected
    }
    checks = {}
    if case["collector"] == "hawk":
        risky = {
            p["id"]: {**expected[p["id"]], "riskLevelDuringSignIn": p["riskLevelDuringSignIn"]}
            for p in selected
            if p["riskLevelDuringSignIn"] in ("low", "medium", "high")
        }
        all_values = {
            p["id"]: {**expected[p["id"]], "riskLevelDuringSignIn": p["riskLevelDuringSignIn"]}
            for p in selected
        }
        for format, suffix in (("JSON", ".json"), ("CSV", ".csv")):
            for label, wanted in (("all", all_values), ("investigate", risky)):
                files = [
                    p
                    for p in paths
                    if p.suffix == suffix
                    and p.name.startswith("_Investigate_") == (label == "investigate")
                ]
                checks[format + "_" + label] = verify_outputs(
                    files, format=format, expected=wanted, source_hashes=sources
                )
    else:
        raw = [p for p in paths if p.suffix == ".json" and "Merged" not in p.parts]
        checks["raw"] = verify_outputs(
            raw, format=case["format"], expected=expected, source_hashes=sources
        )
        if case["merge"] and expected:
            merged = [p for p in paths if p.suffix == ".json" and "Merged" in p.parts]
            checks["merged"] = verify_outputs(
                merged, format=case["format"], expected=expected, source_hashes=sources
            )
    for check in checks.values():
        check["source_hashes"] = {
            Path(p).relative_to(ROOT).as_posix(): v for p, v in check["source_hashes"].items()
        }
        for output in [*check["outputs"], *check["invalid_outputs"]]:
            output["path"] = Path(output["path"]).relative_to(ROOT).as_posix()
    return dict(
        passed=not result["error"] and all(c["passed"] for c in checks.values()),
        source_integrity=all(sha(path) == digest for path, digest in sources.items()),
        mode=case["mode"],
        function=case["function"],
        parameters=case,
        revision=case["revision"],
        checks=checks,
        outputs=[o for c in checks.values() for o in c["outputs"]],
        invalid_outputs=[o for c in checks.values() for o in c["invalid_outputs"]],
        output_count=sum(c["output_count"] for c in checks.values()),
        expected_ids=sorted(expected),
        source_hashes={Path(p).relative_to(ROOT).as_posix(): v for p, v in sources.items()},
        telemetry_calls=result["telemetry_calls"],
        error=result["error"],
        retry_observations=[],
        denial_observations=[],
    )


def cases():
    for mode in ("application", "delegated"):
        yield dict(
            collector="hawk",
            mode=mode,
            function="Get-HawkUserEntraIDSignInLog",
            user="sdk-admin@graph.test",
            format="JSON+CSV",
            merge=False,
            event_types=["interactiveUser"],
        )
        for function in ("Get-GraphEntraSignInLogs", "Get-GraphEntraAuditLogs"):
            for format in ("JSON", "SOF-ELK"):
                yield dict(
                    collector="mes",
                    mode=mode,
                    function=function,
                    user=None,
                    format=format,
                    merge=True,
                    event_types=["All"],
                )
        for all_targets in (False, True):
            yield dict(
                collector="mes",
                mode=mode,
                function="Get-GraphEntraAuditLogs",
                user="sdk-admin@graph.test",
                all=all_targets,
                format="JSON",
                merge=False,
                event_types=[],
            )
        yield dict(
            collector="mes",
            mode=mode,
            function="Get-GraphEntraSignInLogs",
            user="absent-user@graph.test",
            format="JSON",
            merge=False,
            event_types=["All"],
            empty=True,
        )

        for denial in ("permission", "licence"):
            yield dict(
                collector="mes",
                mode=mode,
                function="Get-GraphEntraAuditLogs"
                if denial == "permission"
                else "Get-GraphEntraSignInLogs",
                user=None,
                format="JSON",
                merge=False,
                event_types=["interactiveUser"],
                denial=denial,
            )


def trace_rows():
    result = []
    for name in ("api-state", "api2-state"):
        path = RUNTIME / name / "requests.jsonl"
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    result.append(json.loads(line))
                except ValueError:
                    pass  # Last concurrent append can still be incomplete.
    return result


def admin():
    return httpx.Client(
        base_url="https://localhost:51443",
        verify=ssl.create_default_context(cafile=str(RUNTIME / "instance/ca.pem")),
        trust_env=False,
        timeout=180,
        headers={
            "Authorization": "Bearer " + (RUNTIME / "instance/admin.token").read_text().strip()
        },
    )


def collector_expectations(case, declared, client, base):
    if not case.get("all"):
        return declared
    # MES's literal target OR reaches outside its date window, including evidence
    # already created by SDK/state checks. Snapshot that independent admin surface.
    response = client.get(base + "/evidence/export", params={"family": "directoryAudit"})
    response.raise_for_status()
    snapshot = response.json()
    known = {r["payload"]["id"]: r["payload"] for r in snapshot["records"]}
    for record in declared["records"]:
        if record["family"] == "directoryAudit":
            assert known.get(record["payload"]["id"]) == record["payload"], (
                "Declared collector fixture missing or changed in evidence snapshot"
            )
    return snapshot


def qualify_collectors():
    dataset = seed()
    config = json.loads((STATE / "client.json").read_text())
    base = f"/_mock/tenants/{config['tenant_id']}"
    profiles = {}
    run = "collectors-" + uuid4().hex
    for index, case in enumerate(cases()):
        case.update(
            start="2026-10-15T00:00:00+00:00",
            end="2026-10-16T00:00:00+00:00",
            revision="a44536f2b4225dd8ea2e463db88d5db40cfac8c7"
            if case["collector"] == "hawk"
            else "47565f7842784cd3667ff630e3dccb49fa3408ed",
        )
        name = f"{index:02d}-{case['collector']}-{case['mode']}"
        directory = STATE / run / name
        directory.mkdir(parents=True)
        case.update(
            output_dir=f"/state/{run}/{name}/output", report=f"/state/{run}/{name}/result.json"
        )
        case_path = directory / "case.json"
        case_path.write_text(json.dumps(case, indent=2))
        started = time.time()
        trace_start = __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat()
        saved_sku = None
        retry_case = (
            not case.get("denial")
            and not case.get("empty")
            and (
                case["collector"] == "hawk"
                or (case["function"] == "Get-GraphEntraSignInLogs" and case["format"] == "SOF-ELK")
            )
        )
        with admin() as client:
            expected_dataset = collector_expectations(case, dataset, client, base)
            oracle_path = directory / "expected-dataset.json"
            oracle_path.write_text(json.dumps(expected_dataset, indent=2))
            if retry_case:
                response = client.post(
                    base + "/graph/faults",
                    json={
                        "client_id": config["client_id"]
                        if case["mode"] == "application"
                        else config["public_client_id"],
                        "resource": "signIn",
                        "kind": "next_page_429",
                        "retry_after": 1,
                    },
                )
                response.raise_for_status()
            if case.get("denial") == "licence":
                response = client.get(base + "/licensing/skus")
                response.raise_for_status()
                saved_sku = next(
                    r for r in response.json()["items"] if r["sku_id"] == config["sku_id"]
                )
                saved_sku = {
                    k: v
                    for k, v in saved_sku.items()
                    if k not in {"id", "sku_id", "consumed_units"}
                }
                response = client.put(
                    base + "/licensing/skus/" + config["sku_id"],
                    json={**saved_sku, "status": "Suspended"},
                )
                response.raise_for_status()
        try:
            proc = subprocess.run(
                [
                    *CLIENTS,
                    "run",
                    "--rm",
                    "powershell",
                    "/project/integrations/powershell/Test-GraphCollectors.ps1",
                    "-CasePath",
                    f"/state/{run}/{name}/case.json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=600,
            )
        finally:
            if saved_sku:
                with admin() as client:
                    response = client.put(
                        base + "/licensing/skus/" + config["sku_id"], json=saved_sku
                    )
                    response.raise_for_status()
        (directory / "process.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        report_path = directory / "result.json"
        if not report_path.exists():
            profiles[name] = dict(
                passed=False,
                error="Original collector did not write its execution report",
                exit_code=proc.returncode,
                outputs=[],
                output_count=0,
            )
        else:
            result = json.loads(report_path.read_text(encoding="utf-8-sig"))
            profiles[name] = validate_case(case, result, expected_dataset)
            profiles[name]["exit_code"] = proc.returncode
            profiles[name]["passed"] &= proc.returncode == 0
        observed = [
            r
            for r in trace_rows()
            if r["time"] >= trace_start
            and r["path"].startswith(("/v1.0/auditLogs/", "/beta/auditLogs/"))
        ]
        retries = [r for r in observed if r["status"] == 429 and r.get("continuation")]
        denials = [r for r in observed if r["status"] == 403]
        profiles[name]["retry_observations"] = retries
        profiles[name]["denial_observations"] = denials
        profiles[name]["expected_empty"] = case.get("empty", False)
        profiles[name]["expected_denial"] = case.get("denial")
        if retry_case:
            profiles[name]["passed"] &= bool(retries)
        if case.get("denial"):
            # A denied call is a negative test, never a successful empty collection.
            profiles[name]["passed"] = valid_denial(profiles[name], denials)
        profiles[name]["expected_snapshot"] = {
            "path": oracle_path.relative_to(ROOT).as_posix(),
            "sha256": sha(oracle_path),
        }
        profiles[name]["duration_seconds"] = time.time() - started
        (directory / "verification.json").write_text(json.dumps(profiles[name], indent=2))
        print(
            json.dumps(
                {
                    "collector": name,
                    "passed": profiles[name]["passed"],
                    "records": profiles[name]["output_count"],
                }
            ),
            flush=True,
        )
    result = {
        "status": "passed" if all(p["passed"] for p in profiles.values()) else "failed",
        "profiles": profiles,
    }
    (ROOT / "work/evidence-collectors-report.json").write_text(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(0 if qualify_collectors()["status"] == "passed" else 1)
