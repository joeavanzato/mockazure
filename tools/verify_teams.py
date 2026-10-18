"""Fresh local default lab: original Graph SDK/PowerShell Teams and restart qualification."""

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
    state = directory / "teams-clients"
    state.mkdir()
    base = "/_mock/tenants/" + tenant["id"]

    def call(method, path, body=None, status=200, auth=headers):
        result = client.request(method, path, headers=auth, json=body)
        assert result.status_code == status, (path, result.status_code, result.text[:200])
        return result.json() if result.content else None

    permissions = [
        "Team.ReadBasic.All",
        "Channel.ReadBasic.All",
        "ChannelMessage.Read.All",
        "Chat.Read.All",
    ]
    app = call(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Teams qualification",
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

    raw = b"Teams hosted evidence\x00exact bytes"
    content = upload(raw, "application/octet-stream")
    user = call(
        "POST",
        base + "/identity/users",
        {
            "upn": "teams-reader@" + tenant["domain"],
            "display_name": "Teams reader",
            "password": "local-qualification-password",
        },
        201,
    )
    fixtures = {}

    def record(kind, identifier, payload, content_id=None):
        return {
            "kind": kind,
            "id": identifier,
            "payload": {"id": identifier, **payload},
            **({"content_id": content_id} if content_id else {}),
        }

    def message(identifier, body="Local evidence"):
        return {
            "id": identifier,
            "body": {"contentType": "text", "content": body},
            "importance": "normal",
            "locale": "en-us",
            "messageType": "message",
            "createdDateTime": "2026-10-07T00:00:00Z",
            "lastModifiedDateTime": "2026-10-07T00:00:00Z",
        }

    def edge(sk, sid, relation, tk, tid):
        return {
            "source": {"kind": sk, "id": sid},
            "relation": relation,
            "target": {"kind": tk, "id": tid},
        }

    for label in ("python", "powershell"):
        team, channel, chat = (
            str(uuid4()),
            "19:" + label + "@thread.tacv2",
            "19:" + label + "@thread.v2",
        )
        records = [
            record("team", team, {"displayName": label}),
            record("channel", channel, {"displayName": "Evidence", "membershipType": "standard"}),
        ]
        records += [record("chatMessage", str(i), message(str(i))) for i in range(101)]
        records += [record("chatMessage", "r" + str(i), message("r" + str(i))) for i in range(201)]
        records.append(
            record(
                "chatMessageHostedContent",
                "hosted-one",
                {"contentType": "application/octet-stream"},
                content,
            )
        )
        edges = [edge("team", team, "channels", "channel", channel)]
        edges += [edge("channel", channel, "messages", "chatMessage", str(i)) for i in range(101)]
        edges += [
            edge("chatMessage", "0", "replies", "chatMessage", "r" + str(i)) for i in range(201)
        ]
        edges.append(
            edge("chatMessage", "0", "hostedContents", "chatMessageHostedContent", "hosted-one")
        )
        call(
            "POST",
            base + "/workloads/teams/import",
            {"scope": team, "records": records, "edges": edges},
        )
        chat_records = [record("chat", chat, {"chatType": "group", "topic": label})]
        chat_records += [record("chatMessage", str(i), message(str(i))) for i in range(101)]
        call(
            "POST",
            base + "/workloads/teams/import",
            {
                "scope": chat,
                "records": chat_records,
                "edges": [
                    edge("chat", chat, "messages", "chatMessage", str(i)) for i in range(101)
                ],
            },
        )
        for kind, identifier in (("team", team), ("chat", chat)):
            call(
                "POST",
                base + "/workloads/teams/access",
                {
                    "scope": identifier,
                    "resource_kind": kind,
                    "resource_id": identifier,
                    "principal_kind": "user",
                    "principal_id": user["id"],
                    "permission": "member",
                },
                201,
            )
        fixtures[label] = {"team": team, "channel": channel, "chat": chat}
    origin = "https://localhost:8443"
    config = {
        "tenant_id": tenant["id"],
        "client_id": app["client_id"],
        "user_id": user["id"],
        "fixtures": fixtures,
        "secret_file": "/state/secret.json",
        "ca_file": "/certs/ca.pem",
        "endpoints": {"login": origin, "graph": origin},
        "content_sha256": hashlib.sha256(raw).hexdigest(),
    }
    (state / "client.json").write_text(json.dumps(config), encoding="utf-8")
    (state / "secret.json").write_text(json.dumps({"secret": secret["secret"]}), encoding="utf-8")
    check("typed_teams_chat_replies_and_exact_hosted_content")
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

    image = "mockazure:teams-python-" + directory.name.removeprefix("default-smoke-")
    command(["docker", "build", "-f", "integrations/Dockerfile.python", "-t", image, "."], cwd=ROOT)
    vendor = ROOT / "work/teams-vendor"
    prepare(ROOT / "integrations/teams-manifest.json", vendor, verify_only=True)

    def python_run(phase):
        command(common + [image, "/project/integrations/python/teams.py", phase])

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
                "/project/integrations/powershell/Test-TeamsEvidence.ps1",
                "-Phase",
                phase,
            ]
        )

    python_run("prepare")
    check("original_python_sdk_team_channel_chat_replies_and_hosted_bytes")
    powershell_run("prepare")
    check("original_powershell_team_channel_chat_replies_and_hosted_bytes")
    for fixture in fixtures.values():
        edited = message("1", "Edited after snapshot")
        edited["lastModifiedDateTime"] = "2026-10-08T00:00:00Z"
        call(
            "POST",
            base + "/workloads/teams/import",
            {"scope": fixture["chat"], "records": [record("chatMessage", "1", edited)]},
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
        raise TimeoutError("Local Teams restart readiness failed")
    python_run("resume")
    powershell_run("resume")
    check("both_original_clients_resume_snapshot_after_database_and_api_restart")
    prepare(ROOT / "integrations/teams-manifest.json", vendor, verify_only=True)
    check("original_module_bytes_unchanged")
    reports = {
        name: json.loads((state / name).read_text(encoding="utf-8-sig"))
        for name in ("python-report.json", "powershell-report.json")
    }
    (directory / "teams-report.json").write_text(
        json.dumps(reports, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    smoke(qualify=qualify)
