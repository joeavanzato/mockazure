"""Fresh local default lab: original Graph SDK/PowerShell Intune and restart qualification."""

import json
import time
from uuid import uuid4

import httpx

from tools.intune_fixtures import payload
from tools.prepare_clients import prepare
from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT
from tools.verify_default import main as smoke


def qualify(directory, command, compose, client, headers, tenant, check):
    state = directory / "intune-clients"
    state.mkdir()
    base = "/_mock/tenants/" + tenant["id"]

    def call(method, path, body=None, status=200, auth=headers):
        result = client.request(method, path, headers=auth, json=body)
        assert result.status_code == status, (path, result.status_code, result.text[:200])
        return result.json() if result.content else None

    permissions = [
        "DeviceManagementManagedDevices.Read.All",
        "DeviceManagementConfiguration.Read.All",
    ]
    app = call(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Intune qualification",
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

    call(
        "PUT",
        base + "/licensing/skus/061f9ace-7d42-4136-88ac-31dc755f143f",
        {
            "sku_part_number": "INTUNE_A",
            "status": "Enabled",
            "enabled": 1000,
            "service_plans": [
                {
                    "servicePlanId": "c1ec4a95-1f05-45b3-a911-aa3fa01094f5",
                    "servicePlanName": "INTUNE_A",
                    "provisioningStatus": "Success",
                    "appliesTo": "User",
                }
            ],
        },
    )
    user = call(
        "POST",
        base + "/identity/users",
        {
            "upn": "intune-reader@" + tenant["domain"],
            "display_name": "Intune reader",
            "password": "local-qualification-password",
        },
        201,
    )
    records, edges = [], []

    def record(kind, identifier, derived=None, **values):
        return {
            "kind": kind,
            "id": identifier,
            "payload": payload(derived or kind, identifier, **values),
        }

    def edge(sk, sid, relation, tk, tid):
        return {
            "source": {"kind": sk, "id": sid},
            "relation": relation,
            "target": {"kind": tk, "id": tid},
        }

    records.append(record("detectedApp", "detected", displayName="Evidence app"))
    for i in range(101):
        identifier = f"m{i:03}"
        records.append(
            record(
                "managedDevice",
                identifier,
                deviceName="Before",
                userId=user["id"],
                complianceState="unknown",
            )
        )
        edges += [
            edge("user", user["id"], "managedDevices", "managedDevice", identifier),
            edge("detectedApp", "detected", "managedDevices", "managedDevice", identifier),
        ]
    for platform in ("windows10", "ios", "android", "macOS"):
        for kind in ("deviceConfiguration", "deviceCompliancePolicy"):
            derived = platform + (
                "CompliancePolicy"
                if kind == "deviceCompliancePolicy"
                else "GeneralConfiguration"
                if platform == "windows10"
                else "GeneralDeviceConfiguration"
            )
            records.append(record(kind, derived, derived=derived))
            assignment = kind + "Assignment"
            records.append(
                record(
                    assignment,
                    derived + "-assignment",
                    target={"@odata.type": "#microsoft.graph.allDevicesAssignmentTarget"},
                )
            )
            edges.append(edge(kind, derived, "assignments", assignment, derived + "-assignment"))
            status_kind = (
                "deviceComplianceDeviceStatus"
                if kind == "deviceCompliancePolicy"
                else "deviceConfigurationDeviceStatus"
            )
            count = 101 if derived == "windows10CompliancePolicy" else 1
            for i in range(count):
                identifier = derived + f"-status-{i:03}"
                records.append(record(status_kind, identifier, status="unknown"))
                edges.append(edge(kind, derived, "deviceStatuses", status_kind, identifier))
    call(
        "POST",
        base + "/workloads/intune/import",
        {"scope": "deviceManagement", "records": records, "edges": edges},
    )
    origin = "https://localhost:8443"
    config = {
        "tenant_id": tenant["id"],
        "client_id": app["client_id"],
        "user_id": user["id"],
        "secret_file": "/state/secret.json",
        "ca_file": "/certs/ca.pem",
        "endpoints": {"login": origin, "graph": origin},
    }
    (state / "client.json").write_text(json.dumps(config), encoding="utf-8")
    (state / "secret.json").write_text(json.dumps({"secret": secret["secret"]}), encoding="utf-8")
    check("licensed_intune_inventory_and_eight_platform_policy_types")
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

    image = "mockazure:intune-python-" + directory.name.removeprefix("default-smoke-")
    command(["docker", "build", "-f", "integrations/Dockerfile.python", "-t", image, "."], cwd=ROOT)
    vendor = ROOT / "work/intune-clients-vendor"
    prepare(ROOT / "integrations/intune-manifest.json", vendor, verify_only=True)

    def python_run(phase):
        command(common + [image, "/project/integrations/python/intune.py", phase])

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
                "/project/integrations/powershell/Test-IntuneEvidence.ps1",
                "-Phase",
                phase,
            ]
        )

    python_run("prepare")
    check("original_python_sdk_intune_inventory_policies_statuses")
    powershell_run("prepare")
    check("original_powershell_intune_inventory_policies_statuses")
    call(
        "POST",
        base + "/workloads/intune/import",
        {
            "scope": "deviceManagement",
            "records": [record("managedDevice", "m050", deviceName="After")],
        },
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
        raise TimeoutError("Local Intune restart readiness failed")
    python_run("resume")
    powershell_run("resume")
    check("both_original_clients_resume_snapshot_after_database_and_api_restart")
    prepare(ROOT / "integrations/intune-manifest.json", vendor, verify_only=True)
    check("original_module_bytes_unchanged")
    reports = {
        name: json.loads((state / name).read_text(encoding="utf-8-sig"))
        for name in ("python-report.json", "powershell-report.json")
    }
    (directory / "intune-report.json").write_text(
        json.dumps(reports, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    smoke(qualify=qualify)
