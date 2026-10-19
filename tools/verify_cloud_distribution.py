"""Exercise representative A-F behaviors in installed wheel, sdist and Docker artifacts."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

from tools.build_release import (
    PROBE,
    ROOT,
    sha,
    validate_artifact_report,
    validate_cloud_distribution,
)
from tools.release_report import source_fingerprint
from tools.verify_cloud_workflows import source_identity
from tools.verify_release import UV

CASES = {
    "A": "tests/integration/test_exchange_unified_audit.py::test_search_session_resume",
    "B": (
        "tests/integration/test_cloud_resource_inventory.py::test_related_compute_network_inventory"
    ),
    "C": "tests/integration/test_blob_workflows.py::test_blob_block_and_version_roundtrip",
    "D": (
        "tests/integration/test_structured_service_lifecycle.py::"
        "test_secret_recovery_and_protection"
    ),
    "E": "tests/integration/test_graph_send_workflows.py::test_draft_reply_forward",
    "F": "tests/integration/test_diagnostic_delivery.py::test_storage_keyvault_diagnostics",
}


def validate_junit(path):
    cases = ElementTree.parse(path).getroot().findall(".//testcase")
    expected = {
        (module[:-3].replace("/", "."), name)
        for module, name in (node.split("::") for node in CASES.values())
    }
    actual = {(case.get("classname"), case.get("name")) for case in cases}
    if (
        len(cases) != len(expected)
        or actual != expected
        or any(
            case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")
        )
    ):
        raise ValueError("Every distribution requires all six successful behavior test cases")
    return len(cases)


def qualify(database_container, path):
    commit, fingerprint = source_identity()
    artifact_path = ROOT / "work/release/artifacts.json"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    validate_artifact_report(artifact, ROOT, source_fingerprint())
    assert artifact["source_commit"] == commit
    directory = ROOT / "work/release" / ("cloud-probes-" + uuid4().hex[:10])
    directory.mkdir()
    report = {
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "artifactManifestSha256": sha(artifact_path.read_bytes()),
        "passed": False,
        "distributions": {},
    }
    started = time.monotonic()

    def run(args, label, *, cwd=ROOT, env=None, capture=False):
        with (directory / (label + ".log")).open("w", encoding="utf-8") as log:
            result = subprocess.run(
                [str(v) for v in args],
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE if capture else log,
                stderr=log,
                text=True,
                encoding="utf-8",
                timeout=1200,
            )
        if result.returncode:
            raise RuntimeError(
                "Distribution check failed; inspect " + str(directory / (label + ".log"))
            )
        return result.stdout if capture else None

    try:
        # Fixture source is explicitly tracked; do not copy untracked/runtime files.
        fixture = directory / "fixtures"
        fixture.mkdir()
        tracked = subprocess.check_output(
            ["git", "ls-files", "tests", "pyproject.toml"], cwd=ROOT, text=True
        ).splitlines()
        for relative in tracked:
            target = fixture / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(
                subprocess.check_output(["git", "show", commit + ":" + relative], cwd=ROOT)
            )
        (fixture / "work").mkdir()
        (fixture / ".runtime").mkdir()
        # This file belongs to the locally provisioned test database, not a user tenant.
        shutil.copyfile(ROOT / ".runtime/test-db.password", fixture / ".runtime/test-db.password")
        runtime_lock, dev_lock = directory / "runtime.txt", directory / "test-tools.txt"
        run(
            [
                UV,
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "-o",
                runtime_lock,
            ],
            "runtime-lock",
        )
        run(
            [
                UV,
                "export",
                "--frozen",
                "--only-group",
                "dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "-o",
                dev_lock,
            ],
            "test-lock",
        )
        vendor = directory / "test-tools"
        run(
            [UV, "pip", "install", "--target", vendor, "--require-hashes", "-r", dev_lock],
            "test-tools",
        )
        for kind in ("wheel", "sdist", "docker"):
            xml = fixture / (kind + ".xml")
            if kind != "docker":
                environment = directory / (kind + "-environment")
                run([UV, "venv", "--python", sys.executable, environment], kind + "-environment")
                python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                run(
                    [
                        UV,
                        "pip",
                        "install",
                        "--python",
                        python,
                        "--require-hashes",
                        "-r",
                        runtime_lock,
                    ],
                    kind + "-dependencies",
                )
                package = ROOT / artifact["artifacts"][kind][0]["path"]
                run(
                    [UV, "pip", "install", "--python", python, "--no-deps", package],
                    kind + "-install",
                )
                env = dict(os.environ)
                env.update(
                    PYTHONPATH=os.pathsep.join((str(vendor), str(fixture))),
                    MOCKAZURE_TEST_DB_PORT=os.environ.get("MOCKAZURE_TEST_DB_PORT", "55446"),
                )
                probe = json.loads(
                    run([python, "-c", PROBE], kind + "-probe", cwd=fixture, env=env, capture=True)
                )
                run(
                    [
                        python,
                        "-m",
                        "pytest",
                        *CASES.values(),
                        "--basetemp=work/" + kind,
                        "--junitxml=" + str(xml),
                        "-q",
                    ],
                    kind + "-behaviors",
                    cwd=fixture,
                    env=env,
                )
            else:
                common = [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "container:" + database_container,
                    "--mount",
                    f"type=bind,source={fixture},target=/qualification",
                    "--mount",
                    f"type=bind,source={vendor},target=/test-tools,readonly",
                    "-w",
                    "/qualification",
                    "-e",
                    "PYTHONPATH=/test-tools:/qualification",
                    "-e",
                    "MOCKAZURE_TEST_DB_PORT=5432",
                    "--entrypoint",
                    "python",
                    artifact["image"]["name"],
                ]
                probe = json.loads(run([*common, "-c", PROBE], "docker-probe", capture=True))
                run(
                    [
                        *common,
                        "-m",
                        "pytest",
                        *CASES.values(),
                        "--basetemp=work/docker",
                        "--junitxml=/qualification/docker.xml",
                        "-q",
                    ],
                    "docker-behaviors",
                )
            suites = ElementTree.parse(xml).getroot().findall("testsuite")
            failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
            skipped = sum(int(s.get("skipped", 0)) for s in suites)
            count = sum(int(s.get("tests", 0)) for s in suites)
            assert failed == skipped == 0 and count == validate_junit(xml)
            assert probe["package"] == artifact["package_hashes"]
            report["distributions"][kind] = {
                "passed": True,
                "caseNames": list(CASES),
                "passedTests": count,
                "failed": failed,
                "skipped": skipped,
                "junitPath": xml.relative_to(ROOT).as_posix(),
                "junitSha256": sha(xml.read_bytes()),
                "probe": probe,
            }
            print(kind + ": six cloud phase behaviors passed", flush=True)
        assert source_identity() == (commit, fingerprint)
        report["passed"] = True
        validate_cloud_distribution(report, commit, fingerprint)
    except Exception:
        report["passed"] = False
        raise
    finally:
        report["elapsedSeconds"] = time.monotonic() - started
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-container", default="mockazure-structured-tests-db")
    parser.add_argument(
        "--report", type=Path, default=ROOT / "work/release/cloud-distribution.json"
    )
    args = parser.parse_args()
    qualify(args.database_container, args.report)


if __name__ == "__main__":
    main()
