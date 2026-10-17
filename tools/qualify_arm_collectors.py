"""Original MES Activity Log outputs are checked without repairing or reshaping files."""

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime/m5"


def validate_cases(cases):
    expected = {
        (mode, discovery, denied)
        for mode in ("application", "delegated")
        for discovery in ("discovered", "explicit")
        for denied in (False, True)
    }
    assert len(cases) == len(expected)
    assert {(c["mode"], c["discovery"], c["denied"]) for c in cases} == expected
    assert all(c["passed"] is True and type(c["denied"]) is bool for c in cases)
    for case in cases:
        assert type(case["native_records"]) is int
        assert case["native_records"] == 0 if case["denied"] else case["native_records"] >= 2


def validate_hashes(values, root):
    assert values, "Missing artifact hashes"
    root = root.resolve()
    for relative, digest in values.items():
        path = Path(relative)
        assert not path.is_absolute() and ".." not in path.parts
        path = (root / path).resolve()
        assert path.is_relative_to(root) and path.is_file(), "Missing or escaped artifact"
        assert re.fullmatch("[0-9a-f]{64}", digest)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, "Changed artifact"


def verify_artifacts(files, expected, *, denied=False):
    actual = []
    for path in files:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        assert isinstance(value, list), "Collector output must be one JSON array"
        assert all(isinstance(item, dict) for item in value)
        actual.extend(value)
    if denied:
        assert not actual, "Denied Activity Log disclosure"
        return 0
    assert expected and files, "Missing positive collector output"
    assert len(actual) == len(expected), "Missing or duplicate Activity Logs"
    indexed = {row["eventDataId"]: row for row in actual}
    assert len(indexed) == len(actual), "Duplicate eventDataId"
    assert set(indexed) == {row["eventDataId"] for row in expected}, "Wrong Activity Log IDs"
    from mockazure.providers.arm.activity import instant

    for row in expected:
        captured = indexed[row["eventDataId"]]
        for field, value in row.items():
            assert field in captured, "Missing Activity Log field: " + field
            if field in {"eventTimestamp", "submissionTimestamp"}:
                assert instant(captured[field])[1] == instant(value)[1], "Lost exact Activity time"
            else:
                assert captured[field] == value, "Changed Activity Log field: " + field
    return len(actual)


def qualify_collectors():
    import subprocess
    import time

    state = RUNTIME / "clients/powershell-state"
    run = state / ("arm-collectors-" + str(time.time_ns()))
    run.mkdir()
    expected = json.loads((RUNTIME / "arm-expected.json").read_text(encoding="utf-8"))
    native_expected = json.loads((RUNTIME / "arm-native-expected.json").read_text(encoding="utf-8"))
    assert len(native_expected) >= 2
    cases, source_hashes, output_hashes = [], {}, {}
    for name in ("arm-expected.json", "arm-native-expected.json", "runtime-native-expected.json"):
        output_hashes[name] = hashlib.sha256((RUNTIME / name).read_bytes()).hexdigest()
    records = 0
    for mode in ("application", "delegated"):
        for discovery in ("discovered", "explicit"):
            for denied in (False, True):
                label = f"{mode}-{discovery}-{'denied' if denied else 'positive'}"
                directory = run / label
                directory.mkdir()
                output = directory / "output"
                report_path = directory / "report.json"
                case = dict(
                    mode=mode,
                    discovery=discovery,
                    denied=denied,
                    output_dir="/state/" + output.relative_to(state).as_posix(),
                    report="/state/" + report_path.relative_to(state).as_posix(),
                )
                case.update(native_start="2026-10-08T00:00:00Z", native_end="2026-10-08T23:59:59Z")
                case_path = directory / "case.json"
                case_path.write_text(json.dumps(case), encoding="utf-8")
                process = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "-f",
                        "compose.clients.m5.yaml",
                        "run",
                        "--rm",
                        "powershell",
                        "/project/integrations/powershell/Test-ArmCollectors.ps1",
                        "-CasePath",
                        "/state/" + case_path.relative_to(state).as_posix(),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=180,
                )
                (directory / "process.log").write_text(
                    process.stdout + process.stderr, encoding="utf-8"
                )
                result = dict(mode=mode, discovery=discovery, denied=denied, passed=False)
                try:
                    assert report_path.exists(), "Original collector did not write a report"
                    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
                    assert report["invoked"] is True, "Original collector was not invoked"
                    assert process.returncode == 0, "Collector process failed"
                    assert report["source_hashes"], "Missing original source hashes"
                    for container_path, digest in report["source_hashes"].items():
                        assert container_path.startswith("/vendor/")
                        relative = container_path.removeprefix("/vendor/")
                        validate_hashes({relative: digest}, ROOT / "work/vendor")
                        source_hashes[relative] = digest
                    files = []
                    for name in report["files"]:
                        assert name.startswith("/state/")
                        local = (state / name.removeprefix("/state/")).resolve()
                        assert local.is_relative_to(output.resolve()) and local.is_file()
                        if local.suffix.lower() != ".log":
                            assert local.suffix.lower() == ".json", "Unexpected collector artifact"
                            files.append(local)
                    native_files = [p for p in files if "native" in p.relative_to(output).parts]
                    historical_files = [p for p in files if p not in native_files]
                    count = verify_artifacts(historical_files, expected, denied=denied)
                    native_count = verify_artifacts(native_files, native_expected, denied=denied)
                    requests = report["requests"]
                    assert all(r["local"] is True for r in requests)
                    if denied:
                        assert report["denied_preflight_status"] == 403, (
                            "Denial was not demonstrated"
                        )
                        # Empty discovery and explicit subscription lookup are legitimate
                        # terminal outcomes for this identity with no visible subscriptions.
                        assert report.get("error") is None or (
                            report.get("error_type")
                            in {
                                "Microsoft.Rest.Azure.CloudException",
                                "Microsoft.Azure.Commands.Profile.Models.PSAzureSubscription",
                                "Microsoft.PowerShell.Commands.HttpResponseException",
                                "System.Management.Automation.RuntimeException",
                                "System.Management.Automation.PSArgumentException",
                            }
                            and any(
                                word in report["error"].lower()
                                for word in ("subscription", "403", "forbidden")
                            )
                        ), "Unrelated error is not permission-denial evidence"
                    else:
                        assert report.get("error") is None
                        assert (
                            len(
                                [
                                    r
                                    for r in requests
                                    if "/eventtypes/management/values" in r["path"]
                                ]
                            )
                            >= 11
                        )
                    records += count
                    result.update(
                        passed=True,
                        records=count,
                        native_records=native_count,
                        request_count=len(requests),
                    )
                except (AssertionError, KeyError, ValueError, OSError) as error:
                    result["error"] = str(error) or type(error).__name__
                for artifact in directory.rglob("*"):
                    if artifact.is_file():
                        output_hashes[artifact.relative_to(RUNTIME).as_posix()] = hashlib.sha256(
                            artifact.read_bytes()
                        ).hexdigest()
                cases.append(result)
                print(
                    label + ": " + ("PASS" if result["passed"] else "FAIL " + result["error"]),
                    flush=True,
                )
    result = dict(
        passed=all(c["passed"] for c in cases),
        cases=cases,
        records_verified=records,
        source_hashes=source_hashes,
        output_hashes=output_hashes,
    )
    (ROOT / "work/arm-collectors.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    raise SystemExit(0 if qualify_collectors()["passed"] else 1)
