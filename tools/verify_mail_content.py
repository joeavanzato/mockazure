"""Fresh local default lab: original Graph SDK/PowerShell mail and restart qualification."""

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
    state = directory / "mail-content-clients"
    state.mkdir()
    base = "/_mock/tenants/" + tenant["id"]

    def call(method, path, body=None, status=200, auth=headers):
        result = client.request(method, path, headers=auth, json=body)
        assert result.status_code == status, (path, result.status_code, result.text[:200])
        return result.json() if result.content else None

    permissions = ["Mail.ReadWrite", "MailboxSettings.Read"]
    app = call(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Mail content qualification",
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
    call("PUT", base + "/mail/settings", {"exchange_enabled": True, "audit_tier": "standard"})
    mime, attachment = b"Subject: Original bytes\r\n\r\nBody\x00evidence", b"Attachment\x00bytes"

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

    mime_id, attachment_id = (
        upload(mime, "message/rfc822"),
        upload(attachment, "application/octet-stream"),
    )
    users = {}
    for label in ("python", "powershell"):
        upn = label + "@" + tenant["domain"]
        user = call(
            "POST",
            base + "/identity/users",
            {"upn": upn, "display_name": label, "password": "local-mail-content-password"},
            201,
        )["id"]
        users[label + "_user"] = user
        call("PUT", base + "/mail/mailboxes/" + user, {"primary_smtp": upn, "alias": label})
        call(
            "POST",
            base + "/mail/content/import",
            {
                "mailbox_user_id": user,
                "mode": "content-backed",
                "expected_revision": 0,
                "folders": [
                    {"id": name, "displayName": name.title(), "well_known_name": name}
                    for name in ("inbox", "archive")
                ],
                "messages": [
                    {
                        "id": f"m{i}",
                        "folder_id": "inbox",
                        "mime_content_id": mime_id if i == 0 else None,
                        "payload": {
                            "subject": f"Evidence {i}",
                            "receivedDateTime": "2026-10-07T00:00:00Z",
                            "isRead": False,
                        },
                    }
                    for i in range(101)
                ],
                "attachments": [
                    {
                        "message_id": "m0",
                        "id": "a1",
                        "kind": "fileAttachment",
                        "content_id": attachment_id,
                        "payload": {"name": "evidence.bin"},
                    }
                ],
            },
        )
    origin = "https://localhost:8443"
    config = {
        "tenant_id": tenant["id"],
        "client_id": app["client_id"],
        **users,
        "secret_file": "/state/secret.json",
        "ca_file": "/certs/ca.pem",
        "endpoints": {"login": origin, "graph": origin},
        "mime_sha256": hashlib.sha256(mime).hexdigest(),
        "attachment_sha256": hashlib.sha256(attachment).hexdigest(),
    }
    (state / "client.json").write_text(json.dumps(config), encoding="utf-8")
    (state / "secret.json").write_text(json.dumps({"secret": secret["secret"]}), encoding="utf-8")
    check("typed_mail_fixture_and_exact_binary_content")
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

    image = "mockazure:mail-content-python-" + directory.name.removeprefix("default-smoke-")
    command(["docker", "build", "-f", "integrations/Dockerfile.python", "-t", image, "."], cwd=ROOT)
    vendor = ROOT / "work/mail-content-vendor"
    prepare(ROOT / "integrations/mail-content-manifest.json", vendor, verify_only=True)

    def python_run(phase):
        command(common + [image, "/project/integrations/python/mail_content.py", phase])

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
                "/project/integrations/powershell/Test-MailContent.ps1",
                "-Phase",
                phase,
            ]
        )

    python_run("prepare")
    check("original_python_sdk_mail_pages_raw_bytes_and_delta")
    powershell_run("prepare")
    check("original_powershell_mail_pages_raw_bytes_and_delta")
    token = client.post(
        f"/{tenant['id']}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": app["client_id"],
            "client_secret": secret["secret"],
            "scope": "https://graph.microsoft.com/.default",
        },
    )
    assert token.status_code == 200
    auth = {"Authorization": "Bearer " + token.json()["access_token"]}
    for user in users.values():
        call(
            "POST", f"/v1.0/users/{user}/messages/m1/move", {"destinationId": "archive"}, 201, auth
        )
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
        raise TimeoutError("Local mail restart readiness failed")
    python_run("resume")
    powershell_run("resume")
    check("both_original_clients_resume_delta_after_database_and_api_restart")
    prepare(ROOT / "integrations/mail-content-manifest.json", vendor, verify_only=True)
    check("original_module_bytes_unchanged")
    reports = {
        name: json.loads((state / name).read_text(encoding="utf-8-sig"))
        for name in ("python-report.json", "powershell-report.json")
    }
    (directory / "mail-content-report.json").write_text(
        json.dumps(reports, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    smoke(qualify=qualify)
