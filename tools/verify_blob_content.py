"""C2 original Blob SDK content proof plus the four namespace ecosystems."""

import json
import sys

from tools.verify_default import ROOT
from tools.verify_storage_namespaces import qualify as namespaces


def qualify(directory, command, compose, client, headers, tenant, check):
    cohort = namespaces(directory, command, compose, client, headers, tenant, check)
    state = max(directory.glob("storage-clients-*"), key=lambda p: p.stat().st_mtime)
    report = state / "python-blob-report.json"
    command(
        [
            sys.executable,
            "-m",
            "integrations.python.storage_blob",
            "--profile",
            str(state / "python.json"),
            "--report",
            str(report),
        ],
        cwd=ROOT,
        timeout=300,
    )
    result = json.loads(report.read_text())
    assert result["passed"] and result["bytes"] > 1024 * 1024
    check("storage_blob_original_python_workflow")
    from tools.verify_cloud_workflows import source_identity

    commit, fingerprint = source_identity()
    result = {
        "phase": "C2",
        "passed": True,
        "localOnly": True,
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "namespaces": cohort,
        "blob": result,
    }
    (directory / "storage-blobs-report.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    from tools.prepare_storage_clients import prepare
    from tools.verify_default import main

    prepare()
    main(qualify=qualify)
