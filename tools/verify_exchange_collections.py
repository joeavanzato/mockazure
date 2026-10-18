"""Original Exchange commands and MES transport collector against a fresh local lab."""

import json
from uuid import uuid4

from tools.verify_collections import PS_IMAGE
from tools.verify_default import ROOT
from tools.verify_default import main as smoke


def qualify(directory, command, compose, client, headers, tenant, check, *, workflows=False):
    state = directory / "exchange-clients"
    state.mkdir()
    base = "/_mock/tenants/" + tenant["id"]

    def request(method, path, value, status=200):
        response = client.request(method, path, headers=headers, json=value)
        assert response.status_code == status, (path, response.status_code)
        return response.json()

    permissions = [
        {"resource": "exchange", "kind": "application", "permissions": ["Exchange.ManageAsApp"]},
        {"resource": "graph", "kind": "application", "permissions": ["Directory.Read.All"]},
    ]
    app = request(
        "POST",
        base + "/identity/applications",
        {
            "display_name": "Exchange collections",
            "client_mode": "confidential",
            "required_permissions": permissions,
        },
        201,
    )
    app_id = app["client_id"]
    secret = request(
        "POST", base + f"/identity/applications/{app_id}/credentials", {"kind": "secret"}, 201
    )
    for permission in permissions:
        request("PUT", base + f"/identity/grants/{uuid4()}", {"client_id": app_id, **permission})
    request("PUT", base + f"/identity/applications/{app_id}/roles/Exchange Administrator", {})
    user = request(
        "POST",
        base + "/identity/users",
        {
            "upn": "collection@" + tenant["domain"],
            "display_name": "Collection owner",
            "password": "local-collection-owner-password",
        },
        201,
    )
    request("PUT", base + "/mail/settings", {"exchange_enabled": True, "audit_tier": "standard"})
    request(
        "PUT",
        base + f"/mail/mailboxes/{user['id']}",
        {"primary_smtp": user["upn"], "alias": "collection"},
    )
    fixture = {
        "organization": {"AuditDisabled": True, "OAuth2ClientProfileEnabled": True},
        "transport": {"SmtpClientAuthenticationDisabled": True},
        "accepted_domains": [
            {
                "Guid": str(uuid4()),
                "Name": "Local domain",
                "DomainName": tenant["domain"],
                "DomainType": "Authoritative",
                "Default": True,
            }
        ],
        "transport_rules": [
            {
                "Guid": str(uuid4()),
                "Name": "Forwarding review",
                "Priority": 0,
                "State": "Enabled",
                "Mode": "Audit",
                "Description": "Review outbound forwarding",
                "CreatedBy": "local-admin",
                "WhenChanged": "2026-10-07T10:00:00Z",
            }
        ],
        "mailbox_permissions": [
            {
                "id": str(uuid4()),
                "mailbox_user_id": user["id"],
                "User": "NT AUTHORITY\\SELF",
                "AccessRights": ["FullAccess"],
                "Deny": False,
                "IsInherited": False,
            },
            {
                "id": str(uuid4()),
                "mailbox_user_id": user["id"],
                "User": "auditor@contoso.test",
                "AccessRights": ["FullAccess"],
                "Deny": True,
                "IsInherited": True,
            },
        ],
        "recipient_permissions": [
            {
                "id": str(uuid4()),
                "mailbox_user_id": user["id"],
                "Trustee": "auditor@contoso.test",
                "AccessRights": ["SendAs"],
                "AccessControlType": "Allow",
                "IsInherited": False,
                "InheritanceType": "None",
            }
        ],
    }
    request("PUT", base + "/mail/collectionState", fixture)
    origin = "https://localhost:8443"
    config = {
        "tenant_id": tenant["id"],
        "tenant_domain": tenant["domain"],
        "client_id": app_id,
        "user_id": user["id"],
        "mailbox_upn": user["upn"],
        "secret_file": "/state/secret.json",
        "ca_file": "/certs/ca.pem",
        "endpoints": {"login": origin, "graph": origin, "exchange": origin},
    }
    (state / "client.json").write_text(json.dumps(config))
    (state / "secret.json").write_text(json.dumps({"secret": secret["secret"]}))
    check("exchange_collection_fixture")
    if workflows:
        from tools.verify_exchange_workflows import prepare

        prepare(request, base, tenant, state)
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
        "-e",
        "POWERSHELL_TELEMETRY_OPTOUT=1",
        "-e",
        "DOTNET_CLI_TELEMETRY_OPTOUT=1",
        "-e",
        "TZ=UTC",
        "-e",
        "PSModulePath=/vendor/modules:/opt/microsoft/powershell/7/Modules",
        "--mount",
        f"type=bind,source={directory / '.runtime/instance/ca.pem'},target=/certs/ca.pem,readonly",
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
    ]
    # EXO 3.11.0 reconstructs its bootstrap origin without a custom port.
    # A declared localhost:443 profile and TCP relay preserve the original module.
    instance = directory / ".runtime/instance/container.json"
    runtime = json.loads(instance.read_text(encoding="utf-8"))
    runtime["client_profiles"]["host"] = {
        key: "https://localhost" for key in runtime["client_profiles"]["container"]
    }
    instance.write_text(json.dumps(runtime, indent=2) + "\n", encoding="utf-8")
    command(compose + ["restart", "api"])
    proxy_config = state / "native-haproxy.cfg"
    proxy_config.write_text(
        "defaults\n    mode tcp\n    timeout connect 5s\n    timeout client 60s\n"
        "    timeout server 60s\nfrontend native\n    bind :443\n    default_backend local_api\n"
        "backend local_api\n    server local_api 127.0.0.1:8443 check\n"
    )
    proxy_name = "mockazure-exo-" + directory.name.removeprefix("default-smoke-")
    proxy_image = (
        "haproxy:3.2-alpine@sha256:254e6370f01cd654e0744edf607c67f33777c1b6a9dc0d4b28bcba7171460122"
    )
    command(
        [
            "docker",
            "run",
            "-d",
            "--name",
            proxy_name,
            "--network",
            "container:" + api,
            "--mount",
            f"type=bind,source={proxy_config},target=/usr/local/etc/haproxy/haproxy.cfg,readonly",
            proxy_image,
        ]
    )
    try:
        probe = (
            "import ssl,time,urllib.request\n"
            "c=ssl.create_default_context(cafile='/instance/ca.pem')\n"
            "for i in range(60):\n try:\n"
            "  urllib.request.urlopen('https://localhost/_mock/ready',context=c,timeout=2);break\n"
            " except Exception:\n  time.sleep(0.5)\n"
            "else:raise RuntimeError('Local native TLS listener did not become ready')"
        )
        command(["docker", "exec", api, "python", "-c", probe])
        command(common + ["/project/integrations/powershell/Test-ExchangeCollections.ps1"])
        check("original_exchange_six_collection_commands")
        if workflows:
            audit_client = [
                "TZ=/state/test-timezone" if item == "TZ=UTC" else item for item in common
            ]
            command(audit_client + ["/project/integrations/powershell/Test-ExchangeWorkflows.ps1"])
            workflow_report = json.loads(
                (state / "exchange-workflow-report.json").read_text(encoding="utf-8-sig")
            )
            assert workflow_report["passed"] and workflow_report["records"] == 1101
            (directory / "exchange-workflow-report.json").write_text(
                json.dumps(workflow_report, indent=2) + "\n"
            )
            check("original_exchange_unified_audit_1101_exact_ids")
        # Original collector sources are verified against the pinned archive before and after.
        import hashlib
        from datetime import datetime

        from tools.collector_profiles import expected_files
        from tools.qualify_mail_collectors import rows, verify_rows

        pinned = expected_files(ROOT, "original")
        case = {
            "profile": "original",
            "mode": "application",
            "collector": "mes",
            "function": "Get-TransportRules",
            "denied": False,
            "output_dir": "/state/transport-output",
            "report": "/state/transport-collector.json",
        }
        (state / "transport-case.json").write_text(json.dumps(case))
        command(
            common
            + [
                "/project/integrations/powershell/Test-MailCollectors.ps1",
                "-CasePath",
                "/state/transport-case.json",
            ]
        )
        collector = json.loads((state / "transport-collector.json").read_text(encoding="utf-8-sig"))
        assert not collector["error"]
        assert len(collector["source_hashes"]) == 2
        for path, digest in collector["source_hashes"].items():
            relative = path.removeprefix("/vendor/")
            assert relative in pinned
            assert hashlib.sha256(pinned[relative]).hexdigest() == digest
            assert (
                hashlib.sha256((ROOT / "work/vendor" / relative).read_bytes()).hexdigest() == digest
            )
        files = list((state / "transport-output").rglob("*.csv"))
        assert len(files) == 1
        actual = rows(files[0])
        verify_rows(
            actual,
            [
                {
                    "Name": "Forwarding review",
                    "Description": "Review outbound forwarding",
                    "CreatedBy": "local-admin",
                    "State": "Enabled",
                    "Priority": "0",
                    "Mode": "Audit",
                }
            ],
            "Name",
        )
        observed = actual[0]["WhenChanged"]
        formats = ["%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"]
        parsed = None
        for fmt in formats:
            try:
                parsed = datetime.strptime(observed, fmt)
                break
            except ValueError:
                continue
        assert parsed == datetime(2026, 10, 7, 10), "Collector timestamp mismatch"
        check("original_mes_transport_rules_exact_csv")
        collector["output_sha256"] = hashlib.sha256(files[0].read_bytes()).hexdigest()
        (directory / "exchange-collector-report.json").write_text(
            json.dumps(collector, indent=2) + "\n"
        )

        report = json.loads(
            (state / "exchange-collection-report.json").read_text(encoding="utf-8-sig")
        )
        assert report["passed"] is True and report["commands"] == 6
        (directory / "exchange-collection-report.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    finally:
        command(["docker", "rm", "-f", proxy_name])


if __name__ == "__main__":
    smoke(qualify=qualify)
