"""Build a fresh default lab and qualify real SDKs plus unchanged MES CSV output."""

import json
from uuid import uuid4

from tools.verify_default import ROOT
from tools.verify_default import main as smoke

PS_IMAGE = (
    "mcr.microsoft.com/powershell@sha256:"
    "042240d57ec9e47e511033b92625a8d95875ee5860af3015992c248b58a8be81"
)


def qualify(directory, command, compose, client, headers, tenant, check):
    state = directory / "clients"
    state.mkdir()
    base = f"/_mock/tenants/{tenant['id']}"

    def post(path, body, status=200):
        response = client.post(path, headers=headers, json=body)
        assert response.status_code == status, (path, response.status_code, response.text[:200])
        return response.json()

    def register(permissions, label):
        app = post(
            base + "/identity/applications",
            {
                "display_name": label,
                "client_mode": "confidential",
                "required_permissions": [
                    {"resource": "graph", "kind": "application", "permissions": permissions}
                ],
            },
            201,
        )
        identifier = app["client_id"]
        secret = post(
            base + f"/identity/applications/{identifier}/credentials", {"kind": "secret"}, 201
        )
        result = client.put(
            base + f"/identity/grants/{uuid4()}",
            headers=headers,
            json={
                "client_id": identifier,
                "resource": "graph",
                "kind": "application",
                "permissions": permissions,
            },
        )
        assert result.status_code == 200, result.status_code
        (state / (label + ".json")).write_text(json.dumps({"secret": secret["secret"]}))
        return identifier

    permissions = ["SecurityEvents.Read.All", "SecurityAlert.Read.All", "SecurityIncident.Read.All"]
    app = register(permissions, "positive")
    negative = register(["SecurityEvents.Read.All"], "negative")
    origin = "https://localhost:8443"
    config = {
        "tenant_id": tenant["id"],
        "client_id": app,
        "negative_client_id": negative,
        "secret_file": "/state/positive.json",
        "negative_secret_file": "/state/negative.json",
        "ca_file": "/certs/ca.pem",
        "endpoints": {"login": origin, "graph": origin},
    }
    (state / "client.json").write_text(json.dumps(config))
    evidence = [
        {
            "@odata.type": "#microsoft.graph.security.userEvidence",
            "userAccount": {"userPrincipalName": "analyst@contoso.test"},
        }
    ]
    records = {
        "alerts": [
            {
                "id": f"legacy-{i:03d}",
                "azureTenantId": tenant["id"],
                "title": "Stored legacy alert",
                "severity": "high",
                "status": "newAlert",
                "createdDateTime": "2026-10-01T00:00:00.1234567Z",
                "userStates": [{"userPrincipalName": "analyst@contoso.test"}],
            }
            for i in range(301)
        ],
        "alerts_v2": [
            {
                "id": f"alert-{i:03d}",
                "tenantId": tenant["id"],
                "title": "Stored modern alert",
                "severity": "high",
                "status": "new",
                "createdDateTime": "2026-10-01T00:00:00.1234567Z",
                "incidentId": str(i % 5),
                "evidence": evidence,
            }
            for i in range(301)
        ],
        "incidents": [
            {"id": str(i), "displayName": "Stored incident", "severity": "high", "status": "active"}
            for i in range(5)
        ],
        "secureScoreControlProfiles": [
            {
                "id": f"control-{i:03d}",
                "title": f"Control {i:03d}",
                "maxScore": 2.0,
                "controlCategory": "Identity",
                "service": "AzureAD",
            }
            for i in range(101)
        ],
        "secureScores": [
            {
                "id": f"score-{i}",
                "createdDateTime": f"2026-10-0{i + 1}T00:00:00Z",
                "currentScore": 126.25,
                "maxScore": 202.0,
                "controlScores": [
                    {
                        "controlName": f"control-{j:03d}",
                        "score": 1.25,
                        "controlCategory": "Identity",
                    }
                    for j in range(101)
                ],
            }
            for i in range(3)
        ],
    }
    for family, rows in records.items():
        result = post(base + f"/collections/{family}/import", {"records": rows})
        assert result["count"] == len(rows)
    check("all_five_evidence_families_imported")
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
    python_image = "mockazure:collection-python-" + directory.name.removeprefix("default-smoke-")
    command(
        ["docker", "build", "-f", "integrations/Dockerfile.python", "-t", python_image, "."],
        cwd=ROOT,
    )
    command(
        common + [python_image, "/project/integrations/python/collection_evidence.py"], timeout=300
    )
    check("real_python_graph_sdk")
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
            f"type=bind,source={ROOT / 'work/vendor/modules'},target=/vendor/modules,readonly",
            "--mount",
            (
                f"type=bind,source={ROOT / 'work/vendor/collectors'},"
                "target=/vendor/collectors,readonly"
            ),
            PS_IMAGE,
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-File",
            "/project/integrations/powershell/Test-CollectionEvidence.ps1",
        ],
        timeout=300,
    )
    check("real_powershell_sdk_and_original_mes")
    report = {"checks": {}, "client_reports": {}, "source_hashes": {}}
    for name in ["python-report.json", "powershell-report.json"]:
        value = json.loads((state / name).read_text(encoding="utf-8-sig"))
        report["client_reports"][name] = value
    report["source_hashes"] = json.loads((directory / "report.json").read_text())["source_files"]
    (directory / "collection-report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    smoke(qualify=qualify)
