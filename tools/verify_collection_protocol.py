"""Qualify bounded collection protocol behavior with the original PowerShell SDK."""

import json

from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT
from tools.verify_default import main as smoke
from tools.verify_directory_coverage import qualify as directory_qualify


def qualify(directory, command, compose, client, headers, tenant, check):
    directory_qualify(directory, command, compose, client, headers, tenant, check)
    state = directory / "directory-clients"
    config = json.loads((state / "client.json").read_text())
    secret = json.loads((state / "secret.json").read_text())["secret"]
    token = client.post(
        f"/{tenant['id']}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": config["client_id"],
            "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default",
        },
    )
    assert token.status_code == 200
    auth = {"Authorization": "Bearer " + token.json()["access_token"]}

    def group(name):
        response = client.post(
            "/v1.0/groups",
            headers=auth,
            json={
                "displayName": name,
                "mailNickname": name.lower(),
                "mailEnabled": False,
                "securityEnabled": True,
            },
        )
        assert response.status_code == 201, response.status_code
        return response.json()["id"]

    parent = group("ProtocolParent")
    members = [group(f"ProtocolChild{i}") for i in range(23)]
    for member in members:
        result = client.post(
            f"/v1.0/groups/{parent}/members/$ref",
            headers=auth,
            json={"@odata.id": config["endpoints"]["graph"] + f"/v1.0/groups/{member}"},
        )
        assert result.status_code == 204, result.status_code
    added = client.post(
        f"/_mock/tenants/{tenant['id']}/identity/applications",
        headers=headers,
        json={"display_name": "Protocol paging peer", "client_mode": "public"},
    )
    assert added.status_code == 201
    apps = client.get("/v1.0/applications?$select=id", headers=auth)
    assert apps.status_code == 200
    config.update(
        protocol_parent_id=parent,
        protocol_member_ids=members,
        protocol_application_count=len(apps.json()["value"]),
    )
    (state / "client.json").write_text(json.dumps(config))
    fault = client.post(
        f"/_mock/tenants/{tenant['id']}/graph/faults",
        headers=headers,
        json={
            "client_id": config["client_id"],
            "resource": "applications",
            "kind": "next_page_429",
            "retry_after": 1,
        },
    )
    assert fault.status_code == 201
    check("protocol_fixture_and_application_continuation_fault")
    api = command(compose + ["ps", "-q", "api"], capture=True)
    ca = directory / ".runtime/instance/ca.pem"
    command(
        [
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
            "-e",
            "POWERSHELL_TELEMETRY_OPTOUT=1",
            "-e",
            "DOTNET_CLI_TELEMETRY_OPTOUT=1",
            "-e",
            "PSModulePath=/vendor/modules:/opt/microsoft/powershell/7/Modules",
            "--mount",
            f"type=bind,source={ca},target=/certs/ca.pem,readonly",
            "--mount",
            f"type=bind,source={ROOT / 'integrations'},target=/project/integrations,readonly",
            "--mount",
            f"type=bind,source={ROOT / 'work/vendor/modules'},target=/vendor/modules,readonly",
            "--mount",
            f"type=bind,source={state},target=/state",
            PS_IMAGE,
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-File",
            "/project/integrations/powershell/Test-CoverageCollections.ps1",
        ]
    )
    check("original_powershell_batch_relationship_paging_count_and_retry")
    report = json.loads((state / "protocol-report.json").read_text(encoding="utf-8-sig"))
    (directory / "protocol-report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    smoke(qualify=qualify)
