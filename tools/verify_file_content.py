"""C3 FileREST SDK gate after the four namespace clients and Blob workflow."""

import json
import sys

from tools.verify_blob_content import qualify as blobs
from tools.verify_default import ROOT


def qualify(directory, command, compose, client, headers, tenant, check):
    cohort = blobs(directory, command, compose, client, headers, tenant, check)
    state = max(directory.glob("storage-clients-*"), key=lambda p: p.stat().st_mtime)
    report = state / "python-file-report.json"
    command(
        [
            sys.executable,
            "-m",
            "integrations.python.storage_file",
            "--profile",
            str(state / "python.json"),
            "--report",
            str(report),
        ],
        cwd=ROOT,
        timeout=300,
    )
    result = json.loads(report.read_text(encoding="utf-8"))
    assert result["passed"] and result["bytes"] > 1048576
    check("storage_file_original_python_workflow")
    from tools.verify_cloud_workflows import source_identity

    commit, fingerprint = source_identity()
    output = {
        "phase": "C3",
        "passed": True,
        "localOnly": True,
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "prior": cohort,
        "file": result,
    }
    (directory / "storage-files-report.json").write_text(
        json.dumps(output, indent=2) + "\n", encoding="utf-8"
    )
    return output


if __name__ == "__main__":
    from tools.prepare_storage_clients import prepare
    from tools.verify_default import main

    prepare()
    main(qualify=qualify)
