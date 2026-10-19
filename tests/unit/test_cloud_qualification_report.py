"""The common client report cannot turn missing/native-client evidence into a pass."""

import copy
from importlib import import_module
from pathlib import Path

import pytest


def validator():
    assert Path("tools/verify_cloud_workflows.py").exists(), "Common workflow runner is missing"
    return import_module("tools.verify_cloud_workflows").validate_report


def sample():
    return {
        "phase": "A",
        "sourceCommit": "a" * 40,
        "sourceFingerprint": "b" * 64,
        "elapsedSeconds": 1.0,
        "ecosystems": {
            key: {"status": "not-applicable", "reason": "Exchange commands require PowerShell"}
            for key in ("python", "dotnet", "typescript")
        }
        | {
            "powershell": {
                "status": "passed",
                "clientVersions": {"ExchangeOnlineManagement": "3.11.0"},
                "cases": [
                    {
                        "workflow": "exchange.role-management",
                        "operationIds": [
                            "Get-RoleGroup",
                            "Get-RoleGroupMember",
                            "Add-RoleGroupMember",
                            "Remove-RoleGroupMember",
                            "Get-ManagementRoleAssignment",
                            "New-ManagementRoleAssignment",
                            "Remove-ManagementRoleAssignment",
                        ],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "role_groups": True,
                            "membership_roundtrip": True,
                            "assignment_roundtrip": True,
                        },
                    },
                    {
                        "workflow": "exchange.security-configuration",
                        "operationIds": [
                            verb + "-" + kind
                            for kind in (
                                "RemoteDomain",
                                "InboundConnector",
                                "OutboundConnector",
                                "AuthenticationPolicy",
                                "OrganizationRelationship",
                                "SharingPolicy",
                            )
                            for verb in ("Get", "New", "Set", "Remove")
                        ]
                        + ["Get-AdminAuditLogConfig", "Set-AdminAuditLogConfig"],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "six_families_roundtrip": True,
                            "audit_ingestion_toggle": True,
                            "configuration_absence": True,
                        },
                    },
                    {
                        "workflow": "exchange.unified-audit",
                        "operationIds": ["Search-UnifiedAuditLog"],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "exact_ids": True,
                            "paging": True,
                            "utc_dates": True,
                            "formatted": True,
                        },
                    },
                    {
                        "workflow": "exchange.recipients-and-delegation",
                        "operationIds": [
                            "Get-Recipient",
                            "Get-EXORecipient",
                            "Get-EXOMailboxPermission",
                            "Get-EXORecipientPermission",
                            "Add-MailboxPermission",
                            "Remove-MailboxPermission",
                            "Add-RecipientPermission",
                            "Remove-RecipientPermission",
                            "Set-Mailbox",
                        ],
                        "transport": "module",
                        "expectedStatus": "completed",
                        "actualStatus": "completed",
                        "assertions": {
                            "identity_selectors": True,
                            "paging": True,
                            "permission_mutations": True,
                            "behalf_mutations": True,
                            "revoked_permissions_absent": True,
                        },
                    },
                ],
            }
        },
    }


def test_qualification_matrix_completeness():
    validate = validator()
    good = sample()
    validate(good, {"python", "powershell", "dotnet", "typescript"})
    missing = copy.deepcopy(good)
    del missing["ecosystems"]["dotnet"]
    with pytest.raises(ValueError):
        validate(missing, {"python", "powershell", "dotnet", "typescript"})
    rest = copy.deepcopy(good)
    rest["ecosystems"]["powershell"]["cases"][2]["transport"] = "rest"
    with pytest.raises(ValueError):
        validate(rest, set(good["ecosystems"]))
    unknown = copy.deepcopy(good)
    unknown["ecosystems"]["powershell"]["cases"][2]["operationIds"] = ["Invented-Command"]
    with pytest.raises(ValueError):
        validate(unknown, set(good["ecosystems"]))
    failed = copy.deepcopy(good)
    failed["ecosystems"]["powershell"]["cases"][2]["assertions"]["paging"] = False
    with pytest.raises(ValueError):
        validate(failed, set(good["ecosystems"]))
    with pytest.raises(ValueError):
        validate(good | {"phase": "all"}, set(good["ecosystems"]))


def test_qualification_rejects_unpinned_client_version():
    validate = validator()
    report = sample()
    report["ecosystems"]["powershell"]["clientVersions"]["ExchangeOnlineManagement"] = "0.0.0"
    with pytest.raises(ValueError):
        validate(report, set(report["ecosystems"]))


def test_qualification_rejects_missing_delegation_workflow():
    report = sample()
    report["ecosystems"]["powershell"]["cases"] = [
        case
        for case in report["ecosystems"]["powershell"]["cases"]
        if case["workflow"] != "exchange.recipients-and-delegation"
    ]
    with pytest.raises(ValueError, match="workflow"):
        validator()(report, {"python", "powershell", "dotnet", "typescript"})


def test_qualification_rejects_missing_configuration_workflow():
    report = sample()
    report["ecosystems"]["powershell"]["cases"] = [
        r
        for r in report["ecosystems"]["powershell"]["cases"]
        if r["workflow"] != "exchange.security-configuration"
    ]
    with pytest.raises(ValueError, match="workflow"):
        validator()(report, {"python", "powershell", "dotnet", "typescript"})


def test_qualification_rejects_missing_role_workflow():
    report = sample()
    report["ecosystems"]["powershell"]["cases"] = [
        r
        for r in report["ecosystems"]["powershell"]["cases"]
        if r["workflow"] != "exchange.role-management"
    ]
    with pytest.raises(ValueError, match="workflow"):
        validator()(report, {"python", "powershell", "dotnet", "typescript"})


def test_graph_phase_requires_four_native_workflows():
    import json

    from tools.verify_cloud_workflows import cases

    definition = cases()["E"]
    manifest = json.loads(Path("integrations/cloud-workflows-manifest.json").read_text())
    report = {
        "phase": "E",
        "sourceCommit": "a" * 40,
        "sourceFingerprint": "b" * 64,
        "ecosystems": {},
    }
    for ecosystem in ("python", "powershell", "dotnet", "typescript"):
        report["ecosystems"][ecosystem] = {
            "status": "passed",
            "clientVersions": manifest["phaseClients"]["E"][ecosystem],
            "cases": [
                {
                    "workflow": row["id"],
                    "operationIds": row["operationIds"],
                    "transport": row.get("transportByEcosystem", {}).get(
                        ecosystem, row["transport"]
                    ),
                    "expectedStatus": "completed",
                    "actualStatus": "completed",
                    "assertions": {key: True for key in row["assertions"]},
                }
                for row in definition["workflows"]
            ],
        }
    validator()(report, set(report["ecosystems"]))
    broken = copy.deepcopy(report)
    broken["ecosystems"]["dotnet"]["cases"][0]["transport"] = "rest"
    with pytest.raises(ValueError):
        validator()(broken, set(report["ecosystems"]))
    del report["ecosystems"]["typescript"]
    with pytest.raises(ValueError):
        validator()(report, {"python", "powershell", "dotnet", "typescript"})


def aggregate_sample():
    import json

    from tools.verify_cloud_workflows import cases

    manifest = json.loads(Path("integrations/cloud-workflows-manifest.json").read_text())
    reports = []
    for phase, definition in cases().items():
        if phase not in "ABCDEF":
            continue
        versions = manifest.get("phaseClients", {}).get(phase, manifest["clients"])
        cells = {}
        for ecosystem in ("python", "powershell", "dotnet", "typescript"):
            workflows = [v for v in definition["workflows"] if ecosystem in v["ecosystems"]]
            cells[ecosystem] = (
                {
                    "status": "passed",
                    "clientVersions": versions[ecosystem],
                    "cases": [
                        {
                            "workflow": v["id"],
                            "operationIds": v["operationIds"],
                            "transport": v.get("transportByEcosystem", {}).get(
                                ecosystem, v["transport"]
                            ),
                            "expectedStatus": "completed",
                            "actualStatus": "completed",
                            "assertions": {a: True for a in v["assertions"]},
                        }
                        for v in workflows
                    ],
                }
                if workflows
                else {"status": "not-applicable", "reason": definition["notApplicable"][ecosystem]}
            )
        reports.append(
            {
                "phase": phase,
                "sourceCommit": "a" * 40,
                "sourceFingerprint": "b" * 64,
                "passed": True,
                "ecosystems": cells,
            }
        )
    return {
        "phase": "all",
        "sourceCommit": "a" * 40,
        "sourceFingerprint": "b" * 64,
        "passed": True,
        "phases": reports,
    }


def test_all_phase_qualification_matrix_completeness():
    from tools.verify_cloud_workflows import ECOSYSTEMS

    report = aggregate_sample()
    validator()(report, ECOSYSTEMS)
    for mutation in (
        lambda r: r["phases"].pop(),
        lambda r: r["phases"].append(copy.deepcopy(r["phases"][0])),
        lambda r: r["phases"][1].update(sourceCommit="c" * 40),
        lambda r: r["phases"][2].update(sourceFingerprint="d" * 64),
        lambda r: r["phases"][3].update(passed=False),
        lambda r: r["phases"][4]["ecosystems"].pop("dotnet"),
        lambda r: r["phases"][5]["ecosystems"]["python"]["cases"][0].update(transport="rest"),
    ):
        invalid = copy.deepcopy(report)
        mutation(invalid)
        with pytest.raises(ValueError):
            validator()(invalid, ECOSYSTEMS)


def test_distribution_xml_requires_each_real_behavior(tmp_path):
    import pytest

    from tools.verify_cloud_distribution import CASES, validate_junit

    cases = []
    for node in CASES.values():
        module, name = node.split("::")
        cases.append(f'<testcase classname="{module[:-3].replace("/", ".")}" name="{name}"/>')
    path = tmp_path / "results.xml"
    path.write_text("<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>")
    assert validate_junit(path) == 6
    path.write_text("<testsuites><testsuite>" + "".join(cases[:-1]) + "</testsuite></testsuites>")
    with pytest.raises(ValueError):
        validate_junit(path)
    path.write_text(
        "<testsuites><testsuite>"
        + "".join(cases).replace("/>", "><skipped/></testcase>", 1)
        + "</testsuite></testsuites>"
    )
    with pytest.raises(ValueError):
        validate_junit(path)


def test_exchange_bootstrap_preserves_separate_data_origins(tmp_path):
    from mockazure.config import Settings
    from mockazure.endpoints import EndpointProfile
    from tools.verify_exchange_collections import native_exchange_profile

    host = EndpointProfile(
        login="https://localhost:28443",
        graph="https://localhost:28443",
        exchange="https://localhost:28443",
        arm="https://localhost:28443",
        activity="https://localhost:28443",
        browser_login="https://localhost:28443",
    )
    adapted = native_exchange_profile(host.model_dump())
    assert adapted["exchange"] == "https://localhost"
    for name in ("storage", "file", "queue", "table", "keyvault", "logs"):
        assert adapted[name] == getattr(host, name)
    Settings(
        data_dir=tmp_path,
        database_url="postgresql://localhost/mockazure",
        client_profiles={"host": adapted, "container": EndpointProfile()},
    )


@pytest.mark.parametrize(
    "module_name",
    [
        "tools.verify_graph",
        "tools.verify_evidence",
        "tools.security_runtime",
        "tools.mail_runtime",
        "tools.arm_runtime",
    ],
)
def test_legacy_native_replica_owns_only_its_core_listener(tmp_path, monkeypatch, module_name):
    import importlib
    import json

    from mockazure.config import Settings
    from mockazure.storage.endpoints import listener_ports

    module = importlib.import_module(module_name)
    runtime = tmp_path / "runtime"
    (runtime / "instance").mkdir(parents=True)
    config = {
        "database_url": "postgresql://localhost/mockazure",
        "data_dir": ".",
        "service_endpoints": {"login": "https://login.mockazure.test"},
        "client_profiles": {"host": {"login": "https://localhost:40443"}},
    }
    (runtime / "instance/native.json").write_text(json.dumps(config))
    monkeypatch.setattr(module, "RUNTIME", runtime)
    replica = module.Replica("unused-python", 40444)
    assert listener_ports(Settings(**replica.values)) == (40444,)


def test_exchange_bootstrap_can_add_a_missing_host_profile():
    from tools.verify_exchange_collections import native_exchange_profile

    adapted = native_exchange_profile({"login": "https://localhost:28443"})
    assert adapted["exchange"] == "https://localhost"
    assert adapted["storage"] == "https://localhost:28444"
    assert adapted["logs"] == "https://localhost:28449"


def test_scale_request_measurements_preserve_results_and_exceptions():
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from tools.benchmark_cloud_workflows import RequestMetrics

    metrics = RequestMetrics()
    response = SimpleNamespace(status_code=200)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: metrics.measure("sdk", lambda: response), range(400)))
    assert all(value is response for value in values)
    metrics.measure("rest", lambda: SimpleNamespace(status_code=403))
    metrics.measure("rest", lambda: SimpleNamespace(status_code=503))

    def broken():
        raise OSError("synthetic transport failure")

    with pytest.raises(OSError, match="synthetic transport failure"):
        metrics.measure("rest", broken)
    assert metrics.snapshot() == {
        "scope": "workload-http-transports",
        "attempts": 403,
        "responses": 402,
        "transportFailures": 1,
        "httpErrorResponses": 2,
        "serverErrorResponses": 1,
        "statusCounts": {"200": 400, "403": 1, "503": 1},
        "channelAttempts": {"sdk": 400, "rest": 3},
    }
