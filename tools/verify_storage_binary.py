"""C4 original-client binary, process-restart and cloud restore qualification."""

import hashlib
import json
import sys
import time
from urllib.parse import urlsplit

from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT, wait_for_service_origins
from tools.verify_file_content import qualify as file_workflows


def validate_client_result(ecosystem, result, size):
    expected = hashlib.sha256()
    block = bytes(range(256)) * 4096
    for offset in range(0, size, len(block)):
        expected.update(block[: min(len(block), size - offset)])
    objects = (
        result.get("objects", {})
        if ecosystem in {"python", "typescript"}
        else {"blob": result, "file": result}
    )
    if (
        result.get("passed") is not True
        or set(objects) != {"blob", "file"}
        or any(
            item.get("bytes") != size or item.get("sha256", "").lower() != expected.hexdigest()
            for item in objects.values()
        )
    ):
        raise ValueError("Missing or invalid native Blob/Files byte evidence")


def run_clients(
    directory, command, compose, state, check, *, read_only=False, python_bytes=3145745
):
    ca = str(directory / ".runtime/instance/ca.pem")
    api = command(compose + ["ps", "-q", "api"], capture=True)
    results = {}
    for ecosystem in ("python", "typescript", "dotnet", "powershell"):
        profile = state / (ecosystem + ".json")
        report = state / (ecosystem + "-binary" + ("-read" if read_only else "") + ".json")
        if ecosystem == "python":
            args = [
                sys.executable,
                "-m",
                "integrations.python.storage_binary",
                "--profile",
                str(profile),
                "--report",
                str(report),
            ]
        elif ecosystem == "typescript":
            args = [
                sys.executable,
                "-m",
                "tools.run_cloud_client",
                "--ca",
                ca,
                "--",
                "node",
                str(ROOT / "work/cloud-c-clients/npm/runner.js"),
                "--workflow",
                "storage-binary",
                "--profile",
                str(profile),
                "--report",
                str(report),
            ]
        elif ecosystem == "dotnet":
            args = [
                "dotnet",
                str(ROOT / "work/cloud-c-clients/dotnet/StorageNamespaces.dll"),
                "--workflow",
                "storage-binary",
                "--profile",
                str(profile),
                "--report",
                str(report),
            ]
        else:
            args = [
                "docker",
                "run",
                "--rm",
                "--network",
                "container:" + api,
                "-e",
                "SSL_CERT_FILE=/certs/ca.pem",
                "-e",
                "PSModulePath=/vendor/modules:/opt/microsoft/powershell/7/Modules",
                "-e",
                "POWERSHELL_TELEMETRY_OPTOUT=1",
                "-e",
                "DOTNET_CLI_TELEMETRY_OPTOUT=1",
                "-e",
                "HTTP_PROXY=",
                "-e",
                "HTTPS_PROXY=",
                "-e",
                "ALL_PROXY=",
                "-e",
                "NO_PROXY=*",
                "--mount",
                f"type=bind,source={ca},target=/certs/ca.pem,readonly",
                "--mount",
                f"type=bind,source={ROOT / 'integrations'},target=/project/integrations,readonly",
                "--mount",
                f"type=bind,source={ROOT / 'work/vendor'},target=/vendor,readonly",
                "--mount",
                f"type=bind,source={state},target=/state",
                PS_IMAGE,
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                "/project/integrations/powershell/Test-StorageBinary.ps1",
                "-Profile",
                "/state/" + profile.name,
                "-Report",
                "/state/" + report.name,
            ]
        if ecosystem == "python":
            args += ["--bytes", str(python_bytes)]
        if read_only:
            args += ["-ReadOnly" if ecosystem == "powershell" else "--read-only"]
        command(
            args,
            cwd=ROOT,
            timeout=3600 if ecosystem == "python" and python_bytes == 268435456 else 1800,
        )
        result = json.loads(report.read_text(encoding="utf-8-sig"))
        validate_client_result(
            ecosystem, result, python_bytes if ecosystem == "python" else 3145745
        )
        results[ecosystem] = result
        check("storage_binary_" + ecosystem + ("_read" if read_only else "_write"))
    return results


def cross_read(profile, python_bytes, *, expect_denied=False):
    from urllib.parse import urlsplit

    from azure.core.exceptions import HttpResponseError
    from azure.core.pipeline.transport import RequestsTransport
    from azure.storage.blob import BlobServiceClient
    from azure.storage.fileshare import ShareServiceClient

    from integrations.python.arm import LocalSession

    results = {
        name: {"passed": True, "objects": {}}
        for name in ("python", "typescript", "dotnet", "powershell")
    }
    denied = 0
    for service, cls in (("blob", BlobServiceClient), ("file", ShareServiceClient)):
        endpoint = profile[service + "_endpoint"]
        parsed = urlsplit(endpoint)
        with LocalSession(parsed.scheme + "://" + parsed.netloc, profile["ca_file"]) as session:
            client = cls(
                endpoint,
                credential={"account_name": profile["account"], "account_key": profile["key"]},
                api_version="2025-11-05",
                transport=RequestsTransport(session=session, connection_verify=profile["ca_file"]),
                retry_total=0,
            )
            for ecosystem in results:
                target = (
                    client.get_blob_client(ecosystem + "-evidence", "binary.bin")
                    if service == "blob"
                    else client.get_share_client(ecosystem + "-evidence").get_file_client(
                        "binary.bin"
                    )
                )
                if expect_denied:
                    try:
                        if service == "blob":
                            target.get_blob_properties()
                        else:
                            target.get_file_properties()
                    except HttpResponseError as error:
                        assert error.status_code == 403
                        denied += 1
                    else:
                        raise AssertionError("Pre-restore account key remained valid")
                    break
                digest = hashlib.sha256()
                size = 0
                stream = (
                    target.download_blob(max_concurrency=1)
                    if service == "blob"
                    else target.download_file(max_concurrency=1)
                )
                for part in stream.chunks():
                    digest.update(part)
                    size += len(part)
                results[ecosystem]["objects"][service] = {
                    "bytes": size,
                    "sha256": digest.hexdigest(),
                }
    if expect_denied:
        assert denied == 2
        return {"oldKeyDenied": True}
    for ecosystem, entry in results.items():
        validate_client_result("python", entry, python_bytes if ecosystem == "python" else 3145745)
    return {"objects": 8, "passed": True}


def qualify(directory, command, compose, client, headers, tenant, check, *, binary_only=False):
    prior = (
        None
        if binary_only
        else file_workflows(directory, command, compose, client, headers, tenant, check)
    )
    state = max(directory.glob("storage-clients-*"), key=lambda p: p.stat().st_mtime)
    python_bytes = 3145745 if binary_only else 268435456
    written = run_clients(directory, command, compose, state, check, python_bytes=python_bytes)
    if binary_only:
        return {"debugOnly": True, "written": written}
    command(compose + ["restart", "api", "worker"])
    for _ in range(120):
        try:
            if client.get("/_mock/ready").status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError("Storage restart did not become ready")
    profile = json.loads((state / "python.json").read_text(encoding="utf-8"))
    wait_for_service_origins(
        client,
        ["https://" + urlsplit(profile[name + "_endpoint"]).netloc for name in ("blob", "file")],
    )
    restarted = run_clients(
        directory, command, compose, state, check, read_only=True, python_bytes=python_bytes
    )
    base = "/_mock/tenants/" + tenant["id"] + "/cloud"
    response = client.get(base + "/state", headers=headers)
    assert response.status_code == 200
    snapshot = response.json()
    # Export contains references, never keys; byte store remains authoritative.
    profile = json.loads((state / "python.json").read_text(encoding="utf-8"))
    assert profile["key"] not in json.dumps(snapshot)
    response = client.put(base + "/state", headers=headers, json=snapshot)
    assert response.status_code == 200, response.text
    management = json.loads((state / "management.json").read_text(encoding="utf-8"))
    response = client.post(
        management["account_id"] + "/listKeys",
        params={"api-version": "2025-06-01"},
        headers=management["headers"],
    )
    assert response.status_code == 200
    key = response.json()["keys"][0]["value"]
    assert key != profile["key"]
    revoked = cross_read(profile, python_bytes, expect_denied=True)
    for ecosystem in ("python", "typescript", "dotnet", "powershell"):
        path = state / (ecosystem + ".json")
        value = json.loads(path.read_text(encoding="utf-8"))
        value["key"] = key
        path.write_text(json.dumps(value), encoding="utf-8")
    restored = run_clients(
        directory, command, compose, state, check, read_only=True, python_bytes=python_bytes
    )
    updated_profile = json.loads((state / "python.json").read_text(encoding="utf-8"))
    cross = cross_read(updated_profile, python_bytes)
    check("storage_binary_cross_language_read")
    from tools.verify_cloud_workflows import source_identity

    commit, fingerprint = source_identity()
    result = {
        "phase": "C4",
        "passed": True,
        "localOnly": True,
        "sourceCommit": commit,
        "sourceFingerprint": fingerprint,
        "prior": prior,
        "written": written,
        "restarted": restarted,
        "restored": restored,
        "crossRead": cross,
        "revoked": revoked,
    }
    (directory / "storage-binary-report.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    from tools.prepare_storage_clients import prepare
    from tools.verify_default import main

    prepare()
    main(qualify=qualify)
