"""Qualify directory coverage using a fresh default lab and unchanged Graph clients."""

import json
from uuid import uuid4

from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT
from tools.verify_default import main as smoke


def qualify(directory, command, compose, client, headers, tenant, check):
    state = directory / "directory-clients"
    state.mkdir()
    base = "/_mock/tenants/" + tenant["id"]

    def post(path, body, status=201, auth=headers):
        result = client.post(path, headers=auth, json=body)
        assert result.status_code == status, (path, result.status_code)
        return result.json()

    permissions = ["Directory.Read.All", "Group.ReadWrite.All", "RoleManagement.Read.Directory"]
    app = post(
        base + "/identity/applications",
        {
            "display_name": "Directory qualification",
            "client_mode": "confidential",
            "required_permissions": [
                {"resource": "graph", "kind": "application", "permissions": permissions}
            ],
        },
    )
    app_id = app["client_id"]
    secret = post(base + f"/identity/applications/{app_id}/credentials", {"kind": "secret"})
    granted = client.put(
        base + f"/identity/grants/{uuid4()}",
        headers=headers,
        json={
            "client_id": app_id,
            "resource": "graph",
            "kind": "application",
            "permissions": permissions,
        },
    )
    assert granted.status_code == 200
    raw = client.post(
        f"/{tenant['id']}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": app_id,
            "client_secret": secret["secret"],
            "scope": "https://graph.microsoft.com/.default",
        },
    )
    assert raw.status_code == 200
    graph_headers = {"Authorization": "Bearer " + raw.json()["access_token"]}
    user = post(
        base + "/identity/users",
        {
            "upn": "coverage@" + tenant["domain"],
            "display_name": "Coverage owner",
            "password": "local-coverage-owner-password",
        },
    )
    origin = "https://localhost:8443"
    leaf = post(
        "/v1.0/groups",
        {
            "displayName": "Coverage leaf",
            "mailNickname": "coverageleaf",
            "mailEnabled": False,
            "securityEnabled": True,
        },
        auth=graph_headers,
    )
    group_ids = []
    for i in range(3):
        group = post(
            "/v1.0/groups",
            {
                "displayName": f"Coverage group {i}",
                "mailNickname": f"coverage{i}",
                "mailEnabled": False,
                "securityEnabled": True,
                "owners@odata.bind": [origin + "/v1.0/users/" + user["id"]],
                "members@odata.bind": [origin + "/v1.0/groups/" + leaf["id"]],
            },
            auth=graph_headers,
        )
        group_ids.append(group["id"])
    config = {
        "tenant_id": tenant["id"],
        "client_id": app_id,
        "user_id": user["id"],
        "group_ids": group_ids,
        "leaf_id": leaf["id"],
        "secret_file": "/state/secret.json",
        "ca_file": "/certs/ca.pem",
        "endpoints": {"login": origin, "graph": origin},
    }
    (state / "client.json").write_text(json.dumps(config))
    (state / "secret.json").write_text(json.dumps({"secret": secret["secret"]}))
    imported = client.put(
        base + "/directory/collectionState",
        headers=headers,
        json={"organization": {"city": "Qualification City"}},
    )
    assert imported.status_code == 200
    check("directory_fixture_and_typed_organization")
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
    image = "mockazure:directory-python-" + directory.name.removeprefix("default-smoke-")
    command(["docker", "build", "-f", "integrations/Dockerfile.python", "-t", image, "."], cwd=ROOT)
    command(common + [image, "/project/integrations/python/directory_completion.py"])
    check("original_python_graph_directory_resolution_and_paging")
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
            PS_IMAGE,
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-File",
            "/project/integrations/powershell/Test-DirectoryCompletion.ps1",
        ]
    )
    check("original_powershell_graph_directory_resolution_and_paging")
    report = {
        name: json.loads((state / name).read_text(encoding="utf-8-sig"))
        for name in ("python-report.json", "powershell-report.json")
    }
    (directory / "directory-report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    smoke(qualify=qualify)
