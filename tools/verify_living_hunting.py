"""Qualify original hunting clients against explicit local HTTPS only."""

import argparse
import asyncio
import json
import os
import shutil
import ssl
import subprocess
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from msgraph.generated.security.microsoft_graph_security_run_hunting_query import (
    run_hunting_query_post_request_body,
)

from integrations.python.graph_directory import graph_client
from mockazure.admin.client import AdminClient
from mockazure.asyncio_support import run
from mockazure.config import load_settings
from tools.prepare_clients import prepare
from tools.verify_collections import PS_IMAGE


async def qualify(config, report, *, prepare_clients=False):
    settings = load_settings(config)
    for endpoint in (settings.endpoints.graph, settings.endpoints.login, settings.admin_base_url):
        parts = urlsplit(str(endpoint))
        if parts.scheme != "https" or parts.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("Hunting qualification requires explicit loopback HTTPS")
    vendor = Path("work/hunting-clients").resolve()
    manifest = json.loads(Path("integrations/manifest.json").read_text())
    manifest["artifacts"] = [
        a
        for a in manifest["artifacts"]
        if a["name"] in {"Microsoft.Graph.Authentication", "Microsoft.Graph.Security"}
    ]
    pin = Path("work/hunting-clients-manifest.json")
    pin.parent.mkdir(parents=True, exist_ok=True)
    pin.write_text(json.dumps(manifest))
    prepare(pin, vendor, verify_only=not prepare_clients)
    stamp, device = uuid4().hex[:10], "hunting-" + uuid4().hex
    now = datetime.now(UTC).isoformat()
    async with AdminClient(
        str(settings.admin_base_url), ca_file=settings.ca_file, token_file=settings.token_file
    ) as admin:
        tenant = await admin.request(
            "POST",
            "/_mock/tenants",
            json={
                "slug": "hunting-" + stamp,
                "display_name": "Native hunting qualification",
                "domain": "hunting-" + stamp + ".test",
                "licences": ["E5"],
            },
        )
        tid = tenant["id"]
        base = f"/_mock/tenants/{tid}"
        await admin.request(
            "PATCH",
            base + "/evidence/settings",
            json={
                "clock_mode": "fixed",
                "fixed_time": now,
            },
        )
        await admin.request("PUT", base + "/hunting/settings", json={"entitlements": ["endpoint"]})
        await admin.request(
            "POST",
            base + "/hunting/tables/DeviceInfo/import",
            json={
                "source_ids": ["client-proof"],
                "rows": [
                    {
                        "Timestamp": now,
                        "DeviceId": device,
                        "DeviceName": "native-client.test",
                    }
                ],
            },
        )
        app = await admin.request(
            "POST",
            base + "/identity/applications",
            json={
                "display_name": "Original hunting clients",
                "client_mode": "confidential",
                "required_permissions": [
                    {
                        "resource": "graph",
                        "kind": "application",
                        "permissions": ["ThreatHunting.Read.All"],
                    }
                ],
            },
        )
        credential = await admin.request(
            "POST",
            base + "/identity/applications/" + app["client_id"] + "/credentials",
            json={"kind": "secret"},
        )
        await admin.request(
            "PUT",
            base + "/identity/grants/" + str(uuid4()),
            json={
                "client_id": app["client_id"],
                "resource": "graph",
                "kind": "application",
                "permissions": ["ThreatHunting.Read.All"],
            },
        )
    tls = ssl.create_default_context(cafile=str(settings.ca_file))
    async with httpx.AsyncClient(verify=tls, trust_env=False, follow_redirects=False) as client:
        token_response = await client.post(
            f"{settings.endpoints.login}/{tid}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": app["client_id"],
                "client_secret": credential["secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        if token_response.status_code != 200:
            raise RuntimeError("Local hunting authentication failed")
        token = token_response.json()["access_token"]
        graph = graph_client({"endpoints": {"graph": settings.endpoints.graph}}, token, client)
        result = await graph.security.microsoft_graph_security_run_hunting_query.post(
            run_hunting_query_post_request_body.RunHuntingQueryPostRequestBody(
                query="DeviceInfo | project DeviceId, DeviceName",
                timespan="P1D",
            ),
        )
        assert result.schema[0].name == "DeviceId" and result.schema[0].type == "String"
        assert len(result.results) == 1 and result.results[0].additional_data["DeviceId"] == device
    project = Path.cwd().resolve()
    profile = {
        "endpoint": settings.endpoints.graph,
        "token": token,
        "device_id": device,
        "ca_file": str(settings.ca_file),
        "vendor": str(vendor),
        "relay": False,
    }
    script = "integrations/powershell/Test-LivingHunting.ps1"
    command = [shutil.which("pwsh") or "pwsh", "-NoProfile", "-File", str(project / script)]
    if os.name == "nt":
        profile.update(ca_file="/lab/ca.pem", vendor="/vendor", relay=True)
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--env",
            "SSL_CERT_FILE=/lab/ca.pem",
            "--mount",
            f"type=bind,source={vendor},target=/vendor,readonly",
            "--mount",
            f"type=bind,source={settings.ca_file},target=/lab/ca.pem,readonly",
            "--mount",
            f"type=bind,source={project / 'integrations'},target=/project/integrations,readonly",
            PS_IMAGE,
            "pwsh",
            "-NoProfile",
            "-File",
            "/project/" + script,
        ]
    outcome = await asyncio.to_thread(
        subprocess.run,
        command,
        input=json.dumps(profile),
        text=True,
        capture_output=True,
        timeout=180,
        env=dict(os.environ, SSL_CERT_FILE=str(settings.ca_file)),
    )
    if outcome.returncode:
        error = outcome.stderr.replace(token, "[redacted]").replace(
            credential["secret"], "[redacted]"
        )
        (settings.data_dir / "hunting-qualification-error.log").write_text(error, encoding="utf-8")
        raise RuntimeError("Original hunting cmdlet failed; inspect the redacted local log")
    powershell = json.loads(outcome.stdout[outcome.stdout.index('{"state"') :].strip())
    result = {
        "state": "passed",
        "tenant_id": tid,
        "device_id": device,
        "reference_time": now,
        "api_runtime": "native",
        "powershell_runtime": "isolated-linux" if os.name == "nt" else "native",
        "python": {"client": "msgraph-sdk", "version": version("msgraph-sdk"), "state": "passed"},
        "powershell": powershell,
    }
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--prepare-clients", action="store_true")
    args = parser.parse_args()
    run(qualify(args.config, args.report, prepare_clients=args.prepare_clients))
