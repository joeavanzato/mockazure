"""Fresh local default lab: original Graph SDK/PowerShell site/drive and restart qualification."""

import hashlib
import json
import time
from uuid import uuid4

import httpx

from tools.prepare_clients import prepare
from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT
from tools.verify_default import main as smoke


def qualify(directory, command, compose, client, headers, tenant, check):
    state = directory / "site-drive-clients"
    state.mkdir()
    base = "/_mock/tenants/" + tenant["id"]

    def call(method, path, body=None, status=200, auth=headers):
        result = client.request(method, path, headers=auth, json=body)
        assert result.status_code == status, (path, result.status_code, result.text[:200])
        return result.json() if result.content else None

    permissions = ["Sites.Read.All", "Files.Read.All"]
    app = call(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Site drive qualification",
            "client_mode": "confidential",
            "required_permissions": [
                {"resource": "graph", "kind": "application", "permissions": permissions}
            ],
        },
        201,
    )
    secret = call(
        "POST",
        base + f"/identity/applications/{app['client_id']}/credentials",
        {"kind": "secret"},
        201,
    )
    call(
        "PUT",
        base + f"/identity/grants/{uuid4()}",
        {
            "client_id": app["client_id"],
            "resource": "graph",
            "kind": "application",
            "permissions": permissions,
        },
    )

    def upload(raw, media):
        created = call(
            "POST",
            base + "/content/uploads",
            {"size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "media_type": media},
            201,
        )
        path = base + "/content/uploads/" + created["uploadId"]
        result = client.put(
            path + "/chunks/0",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=raw,
        )
        assert result.status_code == 204
        return call("POST", path + "/complete")["id"]

    raw = b"SharePoint evidence\x00original binary bytes"
    content = upload(raw, "application/octet-stream")
    fixtures = {}
    for label in ("python", "powershell"):
        site, drive, root = f"local.test,{uuid4()},{uuid4()}", label + "!drive", label + "!root"

        def record(kind, identifier, payload, content_id=None):
            return {
                "kind": kind,
                "id": identifier,
                "payload": {
                    "id": identifier,
                    "createdDateTime": "2026-10-07T00:00:00Z",
                    "lastModifiedDateTime": "2026-10-07T00:00:00Z",
                    **payload,
                },
                **({"content_id": content_id} if content_id else {}),
            }

        def edge(sk, sid, relation, tk, tid):
            return {
                "source": {"kind": sk, "id": sid},
                "relation": relation,
                "target": {"kind": tk, "id": tid},
            }

        records = [
            record(
                "site",
                site,
                {
                    "name": label,
                    "displayName": label,
                    "webUrl": "https://local.test/sites/" + label,
                },
            ),
            record("drive", drive, {"name": "Documents", "driveType": "documentLibrary"}),
            record("driveItem", root, {"name": "root", "root": {}, "folder": {"childCount": 101}}),
        ] + [
            record(
                "driveItem",
                label + f"!file{i}",
                {
                    "name": f"Evidence {i}.bin",
                    "size": len(raw),
                    "file": {"mimeType": "application/octet-stream"},
                    "parentReference": {"id": root, "driveId": drive},
                },
                content,
            )
            for i in range(101)
        ]
        edges = [
            edge("site", site, "drives", "drive", drive),
            edge("drive", drive, "root", "driveItem", root),
        ] + [
            edge("driveItem", root, "children", "driveItem", label + f"!file{i}")
            for i in range(101)
        ]
        batch = {"scope": site, "expected_revision": 0, "records": records, "edges": edges}
        call("POST", base + "/workloads/sharepoint/import", batch)
        fixtures[label] = {"site": site, "drive": drive, "root": root, "batch": batch}
    origin = "https://localhost:8443"
    config = {
        "tenant_id": tenant["id"],
        "client_id": app["client_id"],
        "fixtures": {
            label: {k: v for k, v in f.items() if k != "batch"} for label, f in fixtures.items()
        },
        "secret_file": "/state/secret.json",
        "ca_file": "/certs/ca.pem",
        "endpoints": {"login": origin, "graph": origin},
        "content_sha256": hashlib.sha256(raw).hexdigest(),
    }
    (state / "client.json").write_text(json.dumps(config), encoding="utf-8")
    (state / "secret.json").write_text(json.dumps({"secret": secret["secret"]}), encoding="utf-8")
    check("typed_site_drive_fixture_and_exact_binary_content")
    api = command(compose + ["ps", "-q", "api"], capture=True)
    common = [
        "docker",
        "run",
        "--rm",
        "--network",
        "container:" + api,
        "-e",
        "SSL_CERT_FILE=/certs/ca.pem",
        "-e",
        "HTTP_PROXY=",
        "-e",
        "HTTPS_PROXY=",
        "-e",
        "ALL_PROXY=",
        "-e",
        "NO_PROXY=*",
        "--mount",
        f"type=bind,source={directory / '.runtime/instance/ca.pem'},target=/certs/ca.pem,readonly",
        "--mount",
        f"type=bind,source={ROOT / 'integrations'},target=/project/integrations,readonly",
        "--mount",
        f"type=bind,source={state},target=/state",
    ]

    image = "mockazure:site-drive-python-" + directory.name.removeprefix("default-smoke-")
    command(["docker", "build", "-f", "integrations/Dockerfile.python", "-t", image, "."], cwd=ROOT)
    vendor = ROOT / "work/site-drive-vendor"
    prepare(ROOT / "integrations/site-drive-manifest.json", vendor, verify_only=True)

    def python_run(phase):
        command(common + [image, "/project/integrations/python/site_drive.py", phase])

    def powershell_run(phase):
        command(
            common
            + [
                "-e",
                "POWERSHELL_TELEMETRY_OPTOUT=1",
                "-e",
                "DOTNET_CLI_TELEMETRY_OPTOUT=1",
                "-e",
                "PSModulePath=/vendor/modules:/opt/microsoft/powershell/7/Modules",
                "--mount",
                f"type=bind,source={vendor / 'modules'},target=/vendor/modules,readonly",
                PS_IMAGE,
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-File",
                "/project/integrations/powershell/Test-SiteDriveEvidence.ps1",
                "-Phase",
                phase,
            ]
        )

    python_run("prepare")
    check("original_python_sdk_site_drive_pages_all_file_bytes_and_delta")
    powershell_run("prepare")
    check("original_powershell_site_drive_pages_all_file_bytes_and_delta")
    for fixture in fixtures.values():
        batch = fixture["batch"]
        batch["expected_revision"] = 1
        batch["records"][3]["payload"]["name"] = "Renamed evidence.bin"
        deleted = batch["records"].pop(4)["id"]
        batch["edges"] = [e for e in batch["edges"] if e["target"]["id"] != deleted]
        batch["tombstones"] = [{"kind": "driveItem", "id": deleted}]
        call("POST", base + "/workloads/sharepoint/import", batch)
    command(compose + ["restart", "db"])
    command(compose + ["restart", "api"])
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            if client.get("/_mock/ready").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise TimeoutError("Local site/drive restart readiness failed")
    python_run("resume")
    powershell_run("resume")
    check("both_original_clients_resume_delta_after_database_and_api_restart")
    prepare(ROOT / "integrations/site-drive-manifest.json", vendor, verify_only=True)
    check("original_module_bytes_unchanged")
    reports = {
        name: json.loads((state / name).read_text(encoding="utf-8-sig"))
        for name in ("python-report.json", "powershell-report.json")
    }
    (directory / "site-drive-report.json").write_text(
        json.dumps(reports, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    smoke(qualify=qualify)
