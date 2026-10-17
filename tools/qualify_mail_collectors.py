"""Pinned original collector execution and fail-closed artifact oracles."""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
RUNTIME = ROOT / ".runtime/m4"
FUNCTIONS = {
    "hawk_forwarding": ("hawk", "Get-HawkUserEmailForwarding"),
    "hawk_rules": ("hawk", "Get-HawkUserInboxRule"),
    "hawk_configuration": ("hawk", "Get-HawkUserConfiguration"),
    "mes_rules": ("mes", "Get-MailboxRules"),
    "mes_graph_rules": ("mes", "Get-MailboxRulesGraph"),
    "mes_trace": ("mes", "Get-MessageTraceLog"),
    "mes_audit": ("mes", "Get-UALGraph"),
    "mes_audit_csv": ("mes", "Get-UALGraph"),
    "mes_audit_jsonl": ("mes", "Get-UALGraph"),
    "mes_audit_sofelk": ("mes", "Get-UALGraph"),
    "mes_audit_native": ("mes", "Get-UALGraph"),
}


def rows(path):
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, list) else ([] if value is None else [value])


def verify_rows(actual, expected, key):
    assert len(actual) == len(expected), "Missing or duplicate output records"
    indexed = {row[key]: row for row in actual}
    assert len(indexed) == len(actual), "Duplicate output identities"
    assert set(indexed) == {row[key] for row in expected}, "Wrong output identities"
    for row in expected:
        for field, value in row.items():
            assert indexed[row[key]][field] == value, f"Wrong output field: {field}"


def verify_denied(files):
    for path in files:
        if path.suffix.lower() in {".csv", ".json", ".jsonl"}:
            assert not rows(path), f"Denied record disclosure: {path.name}"
        elif path.suffix.lower() == ".txt" and not path.name.startswith("_"):
            assert not path.read_text(encoding="utf-8-sig").strip(), "Denied text disclosure"


def validate_cases(cases):
    assert len(cases) == 4
    assert {(c["mode"], c["denied"]) for c in cases} == {
        (mode, denied) for mode in ("application", "delegated") for denied in (False, True)
    }
    assert all(c["passed"] is True and type(c["denied"]) is bool for c in cases)


def validate_collector_hashes(results, root):
    import hashlib
    import re

    for result in results.values():
        for field, base in (
            ("source_hashes", root / "work/vendor"),
            ("output_hashes", root / ".runtime/m4"),
        ):
            assert result[field], "Missing artifact hashes"
            base = base.resolve()
            for relative, digest in result[field].items():
                path = Path(relative)
                assert not path.is_absolute() and ".." not in path.parts
                path = (base / path).resolve()
                assert path.is_relative_to(base) and path.is_file(), "Artifact escaped or missing"
                assert re.fullmatch("[0-9a-f]{64}", digest), "Invalid artifact digest"
                assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, "Artifact changed"


def run_collectors():
    import shutil
    import ssl
    import subprocess
    import time
    from datetime import UTC, datetime, timedelta

    import httpx

    state = RUNTIME / "clients/powershell-state"
    config = json.loads((state / "client.json").read_text())
    secret = json.loads((state / "secret.json").read_text())["secret"]
    admin = (RUNTIME / "instance/admin.token").read_text().strip()
    stamp = str(time.time_ns())
    with httpx.Client(
        base_url="https://localhost:34443",
        trust_env=False,
        timeout=60,
        verify=ssl.create_default_context(cafile=str(RUNTIME / "instance/ca.pem")),
    ) as client:

        def checked(response):
            response.raise_for_status()
            return response.json() if response.content else None

        def token(resource):
            return checked(
                client.post(
                    f"/{config['tenant_id']}/oauth2/v2.0/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": config["client_id"],
                        "client_secret": secret,
                        "scope": resource + "/.default",
                    },
                )
            )["access_token"]

        graph = {"Authorization": "Bearer " + token("https://graph.microsoft.com")}
        exchange = {"Authorization": "Bearer " + token("https://outlook.office365.com")}
        control = f"/_mock/tenants/{config['tenant_id']}"
        rules_path = f"/v1.0/users/{config['user_id']}/mailFolders/inbox/messageRules"

        def command(name, **parameters):
            return checked(
                client.post(
                    f"/adminapi/v2.0/{config['tenant_id']}/InvokeCommand",
                    headers=exchange,
                    json={"CmdletInput": {"CmdletName": name, "Parameters": parameters}},
                )
            )["value"]

        original = command("Get-Mailbox", Identity=config["mailbox_upn"])[0]
        originals = checked(client.get(rules_path, headers=graph))["value"]
        assert len(originals) == 3
        sweep = hidden = None
        started = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        try:
            command(
                "Set-Mailbox",
                Identity=config["mailbox_upn"],
                ForwardingSmtpAddress="smtp:collector-forward@example.test",
                DeliverToMailboxAndForward=True,
            )
            sweep = command(
                "New-SweepRule",
                Mailbox=config["mailbox_upn"],
                Name="Collector sweep " + stamp,
                Provider="Exchange16",
                Sender="sweep@example.test",
                SourceFolder="inbox",
                DestinationFolder="review",
                KeepForDays=7,
            )[0]
            # Three rules exercise identity/order; one forwarding rule exercises all
            # original Hawk investigation artifacts as a single valid JSON document.
            for rule in originals[1:]:
                checked(
                    client.patch(
                        rules_path + "/" + rule["id"],
                        headers=graph,
                        json={"actions": {"markAsRead": True}},
                    )
                )
            checked(
                client.patch(
                    rules_path + "/" + originals[0]["id"],
                    headers=graph,
                    json={
                        "actions": originals[0]["actions"] | {"delete": True},
                        "conditions": {"subjectContains": ["collector"]},
                    },
                )
            )
            hidden = checked(
                client.post(
                    control + f"/mail/mailboxes/{config['user_id']}/rules",
                    headers={"Authorization": "Bearer " + admin},
                    json={
                        "hidden": True,
                        "rule": {
                            "displayName": "Collector hidden " + stamp,
                            "sequence": 4,
                            "isEnabled": True,
                            "actions": {"markAsRead": True},
                        },
                    },
                )
            )
            ended = datetime.now(UTC).isoformat()
            native = []
            for _ in range(20):
                query = checked(
                    client.post(
                        "/v1.0/security/auditLog/queries",
                        headers=graph,
                        json={
                            "displayName": "Canonical native collector oracle",
                            "filterStartDateTime": started,
                            "filterEndDateTime": ended,
                            "operationFilters": ["Set-Mailbox"],
                            "objectIdFilters": [config["mailbox_upn"]],
                        },
                    )
                )
                item = "/v1.0/security/auditLog/queries/" + query["id"]
                for _ in range(100):
                    status = checked(client.get(item, headers=graph))["status"]
                    assert status != "failed", "Native oracle query failed"
                    if status == "succeeded":
                        break
                    time.sleep(0.2)
                assert status == "succeeded", "Native oracle query timed out"
                native = checked(client.get(item + "/records", headers=graph))["value"]
                if native:
                    break
                time.sleep(0.5)
            assert len(native) == 1 and native[0]["operation"] == "Set-Mailbox"
            expected = {
                "mailbox": command("Get-Mailbox", Identity=config["mailbox_upn"])[0],
                "rules": command("Get-InboxRule", Mailbox=config["mailbox_upn"]),
                "graph_rules": checked(client.get(rules_path, headers=graph))["value"],
                "sweep": [sweep],
                "fixture": config["mail_fixture"],
                "native": native,
                "hidden_id": hidden["id"],
            }
            cases = []
            for mode in ("application", "delegated"):
                for label, (collector, function) in FUNCTIONS.items():
                    for denied in (False, True):
                        name = f"{stamp}-{mode}-{label}" + ("-denied" if denied else "")
                        case = {
                            "mode": mode,
                            "collector": collector,
                            "function": function,
                            "denied": denied,
                            "start": config["mail_fixture"]["audit_start"],
                            "end": config["mail_fixture"]["audit_end"],
                            "output_dir": "/state/collectors/" + name,
                            "report": "/state/" + name + ".json",
                            "hidden_id": hidden["id"],
                            "audit_start": started
                            if label == "mes_audit_native"
                            else config["mail_fixture"]["audit_start"],
                            "audit_end": ended
                            if label == "mes_audit_native"
                            else config["mail_fixture"]["audit_end"],
                            "audit_operations": ["Set-Mailbox"]
                            if label == "mes_audit_native"
                            else ["MailItemsAccessed"],
                            "audit_output": {
                                "mes_audit_csv": "CSV",
                                "mes_audit_jsonl": "JSONL",
                                "mes_audit_sofelk": "SOF-ELK",
                            }.get(label, "JSON"),
                        }
                        path = state / (name + "-case.json")
                        path.write_text(json.dumps(case))
                        cases.append(
                            {
                                "label": label,
                                "mode": mode,
                                "denied": denied,
                                "name": name,
                                "case_file": path.name,
                            }
                        )
            batch = state / ("mail-collectors-" + stamp + ".ps1")
            batch.write_text(
                "$ErrorActionPreference='Stop'\n"
                + "\n".join(
                    "try { & /project/integrations/powershell/Test-MailCollectors.ps1 "
                    f"-CasePath /state/{c['case_file']} }} catch {{ "
                    f"Write-Host 'Case recorded: {c['name']}' }}"
                    for c in cases
                )
                + "\nexit 0\n"
            )
            # Own generated local admin credential, mounted only in the isolated
            # qualification container; removed even when an original collector fails.
            shutil.copyfile(RUNTIME / "instance/admin.token", state / "collector-admin.token")
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-f",
                    "compose.clients.m4.yaml",
                    "run",
                    "--rm",
                    "powershell",
                    "/state/" + batch.name,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
            )
            (ROOT / "work/mail-collectors.log").write_text(
                result.stdout + result.stderr, encoding="utf-8"
            )
            assert result.returncode == 0, "Collector process failed; inspect local log"
            evidence = {"stamp": stamp, "expected": expected, "cases": cases}
            (ROOT / "work/mail-collector-run.json").write_text(json.dumps(evidence, indent=2))
            return evidence
        finally:
            try:
                checked(
                    client.put(
                        control + "/mail/settings",
                        headers={"Authorization": "Bearer " + admin},
                        json={"exchange_enabled": True, "audit_tier": "standard"},
                    )
                )
                for rule in originals:
                    checked(
                        client.patch(
                            rules_path + "/" + rule["id"],
                            headers=graph,
                            json={
                                "actions": rule["actions"],
                                "conditions": rule.get("conditions", {}),
                            },
                        )
                    )
                if hidden:
                    command(
                        "Remove-InboxRule", Mailbox=config["mailbox_upn"], Identity=hidden["id"]
                    )
                if sweep:
                    command(
                        "Remove-SweepRule",
                        Mailbox=config["mailbox_upn"],
                        Identity=sweep["Identity"],
                    )
                command(
                    "Set-Mailbox",
                    Identity=config["mailbox_upn"],
                    ForwardingSmtpAddress=original["ForwardingSmtpAddress"],
                    DeliverToMailboxAndForward=original["DeliverToMailboxAndForward"],
                )
            finally:
                (state / "collector-admin.token").unlink(missing_ok=True)


def csv_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    return str(value)


def normalized_audit_time(record):
    import copy
    from datetime import UTC, datetime

    record = copy.deepcopy(record)
    common = record["auditData"]["dynamicProperties"]
    for value, field in ((record, "createdDateTime"), (common, "CreationTime")):
        parsed = datetime.fromisoformat(value[field].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None, "Audit time lost its timezone"
        value[field] = parsed.astimezone(UTC).isoformat(timespec="microseconds")
    return record


def verify_artifacts(label, files, expected):
    """Validate every data artifact, including duplicate summary exports."""
    import re

    box = expected["mailbox"]
    data_files = [p for p in files if p.suffix.lower() in {".csv", ".json", ".jsonl"}]
    texts = [p for p in files if p.suffix.lower() == ".txt" and not p.name.startswith("_")]
    count = 0
    if label == "hawk_configuration":
        assert not data_files and len(texts) == 5, "Missing mailbox configuration output"
        requirements = {
            "Mailbox_Info": {
                "UserPrincipalName": box["UserPrincipalName"],
                "ArchiveStatus": "Active",
                "ArchiveDatabase": box["ArchiveDatabase"],
            },
            "Mailbox_Statistics": {"ItemCount": "14", "TotalItemSize": "5,120 bytes"},
            "Mailbox_Archive_Statistics": {
                "ItemCount": "3",
                "TotalItemSize": "2,048 bytes",
                "IsArchiveMailbox": "True",
            },
            "Mailbox_Folder_Statistics": {
                "Name": "Inbox",
                "ItemsInFolder": "12",
                "FolderSize": "4,096 bytes",
            },
            "CAS_Mailbox_Info": {
                "ExternalDirectoryObjectId": box["ExternalDirectoryObjectId"],
                "ImapEnabled": "True",
            },
        }
        found = set()
        for path in texts:
            prefix = next((name for name in requirements if path.name.startswith(name + "_")), None)
            assert prefix and prefix not in found, "Unexpected/duplicate mailbox text"
            found.add(prefix)
            content = path.read_text(encoding="utf-8-sig")
            for field, value in requirements[prefix].items():
                assert re.search(
                    r"(?m)^" + field + r"\s*:\s*[^\r\n]*" + re.escape(value), content
                ), f"Wrong {path.name} field {field}"
            if prefix == "Mailbox_Folder_Statistics":
                assert re.search(r"(?m)^Name\s*:\s*Review\s*$", content)
                assert re.search(r"(?m)^ItemsInFolder\s*:\s*2\s*$", content)
                assert len(re.findall(r"(?m)^Name\s*:", content)) == 2
        assert found == requirements.keys()
        return 5
    assert not texts, "Unexpected collector data text"
    assert data_files, "Collector emitted no data artifacts"
    seen = set()
    for path in data_files:
        # SOF-ELK explicitly specifies NDJSON despite its .json suffix.
        if label in {"mes_audit_jsonl", "mes_audit_sofelk"}:
            actual = [
                json.loads(line)["dynamicProperties"]
                for line in path.read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            ]
        else:
            actual = rows(path)  # Other JSON must be one complete document.
        is_csv = path.suffix.lower() == ".csv"
        if label == "hawk_forwarding":
            assert any(
                name in path.name
                for name in (
                    "_Investigate_Users_WithForwarding",
                    "User_ForwardingReport",
                    "ForwardingReport",
                )
            )
            wanted = [
                {
                    key: value
                    for key, value in box.items()
                    if key
                    in {
                        "DisplayName",
                        "UserPrincipalName",
                        "PrimarySmtpAddress",
                        "ForwardingSmtpAddress",
                        "ForwardingAddress",
                        "DeliverToMailboxAndForward",
                    }
                }
            ]
            actual = [{key.lower(): value for key, value in row.items()} for row in actual]
            wanted = [{key.lower(): value for key, value in row.items()} for row in wanted]
            key = "userprincipalname"
        elif label == "hawk_rules":
            sweep = "SweepRules" in path.name
            investigative = "_Investigate_InboxRules" in path.name
            assert sweep or "InboxRules" in path.name
            wanted = expected["sweep"] if sweep else expected["rules"]
            if investigative:
                wanted = [r for r in wanted if r["ForwardTo"]]
            key = "RuleIdentity"
            if is_csv:
                names = {
                    "Identity",
                    "RuleIdentity",
                    "Name",
                    "Enabled",
                    "Priority",
                    "Description",
                    "Provider",
                    "Sender",
                    "SourceFolder",
                    "DestinationFolder",
                    "KeepForDays",
                    "MarkAsRead",
                    "StopProcessingRules",
                    "DeleteMessage",
                }
                wanted = [{k: v for k, v in row.items() if k in names} for row in wanted]
            # JSON keeps recipient Address/Name facts as objects; CSV uses the
            # unmodified Hawk serializer (array type names), with Description intact.
        elif label == "mes_rules":
            key = "RuleIdentity"
            wanted = [
                {
                    "RuleIdentity": r["RuleIdentity"],
                    "UserName": box["UserPrincipalName"],
                    "RuleName": r["Name"],
                    "Priority": r["Priority"],
                    "Enabled": r["Enabled"],
                    "ForwardTo": ", ".join(v["Address"] for v in r["ForwardTo"]),
                    "MarkAsRead": r["MarkAsRead"],
                    "StopProcessingRules": r["StopProcessingRules"],
                    "Description": r["Description"],
                    "DeleteMessage": r["DeleteMessage"],
                    "SubjectContainsWords": ", ".join(r["SubjectContainsWords"] or []),
                }
                for r in expected["rules"]
            ]
        elif label == "mes_graph_rules":
            key = "RuleId"
            wanted = [
                {
                    "RuleId": r["id"],
                    "UserPrincipalName": box["UserPrincipalName"],
                    "RuleName": r["displayName"],
                    "Sequence": r["sequence"],
                    "Enabled": r["isEnabled"],
                    "ForwardTo": "; ".join(
                        v["emailAddress"]["address"] for v in r["actions"].get("forwardTo", [])
                    ),
                    "MarkAsRead": r["actions"].get("markAsRead"),
                    "StopProcessingRules": r["actions"].get("stopProcessingRules"),
                    "Delete": r["actions"].get("delete"),
                    "SubjectContains": "; ".join(
                        r.get("conditions", {}).get("subjectContains", [])
                    ),
                }
                for r in expected["graph_rules"]
            ]
        elif label == "mes_trace":
            key = "MessageTraceId"
            wanted = [
                {
                    key: identifier,
                    "SenderAddress": "sender@example.test",
                    "RecipientAddress": box["UserPrincipalName"],
                    "Status": "Delivered",
                    "Size": 1024 + i,
                    "MessageId": f"<qualified-{i}@example.test>",
                    "Subject": f"Qualified trace {i}",
                    "FromIP": "192.0.2.42",
                    "ToIP": "192.0.2.43",
                }
                for i, identifier in enumerate(expected["fixture"]["trace_ids"])
            ]
        elif label.startswith("mes_audit"):
            key = "id"
            wanted = [
                {
                    "id": identifier,
                    "operation": "MailItemsAccessed",
                    "service": "Exchange",
                    "auditLogRecordType": "ExchangeItem",
                    "userPrincipalName": box["UserPrincipalName"],
                    "objectId": f"qualified-message-{i}",
                    "clientIp": "192.0.2.42",
                }
                for i, identifier in enumerate(expected["fixture"]["audit_ids"])
            ]
            if label == "mes_audit_native":
                wanted = [normalized_audit_time(r) for r in expected["native"]]
                actual = [normalized_audit_time(r) for r in actual]
            if label in {"mes_audit_jsonl", "mes_audit_sofelk"}:
                wanted = [
                    {
                        "Id": r["id"],
                        "Operation": r["operation"],
                        "ObjectId": r["objectId"],
                        "UserId": r["userPrincipalName"],
                        "Workload": "Exchange",
                        "ResultStatus": "Succeeded",
                        "RecordType": 2,
                    }
                    for r in wanted
                ]
                key = "Id"
            for record in actual if label not in {"mes_audit_jsonl", "mes_audit_sofelk"} else []:
                audit_data = json.loads(record["auditData"]) if is_csv else record["auditData"]
                common = audit_data["dynamicProperties"]
                assert common["Id"] == record["id"] and common["Operation"] == record["operation"]
                assert common["ObjectId"] == record["objectId"]
                assert common["UserId"] == (record["userPrincipalName"] or record["userId"])
                assert common["RecordType"] == (1 if label == "mes_audit_native" else 2)
                assert common["ResultStatus"] == (
                    "True" if label == "mes_audit_native" else "Succeeded"
                )
        else:
            raise AssertionError("Unknown collector")
        if is_csv:
            wanted = [{k: csv_value(v) for k, v in r.items()} for r in wanted]
        verify_rows(actual, wanted, key)
        count += len(actual)
        seen.add(path.suffix.lower())
    if label.startswith("hawk_"):
        assert seen == {".csv", ".json"}
        assert len(data_files) == (6 if label == "hawk_forwarding" else 10)
    else:
        assert len(data_files) == 1
    return count


def qualify_collectors(evidence=None):
    from tools.verify_mail import sha

    evidence = run_collectors() if evidence is None else evidence
    state = RUNTIME / "clients/powershell-state"
    results = {
        label: {
            "passed": False,
            "records_verified": 0,
            "source_hashes": {},
            "output_hashes": {},
            "cases": [],
        }
        for label in FUNCTIONS
    }
    for case in evidence["cases"]:
        label, mode, denied, name = (case[k] for k in ("label", "mode", "denied", "name"))
        report = json.loads((state / (name + ".json")).read_text(encoding="utf-8-sig"))
        folder = state / "collectors" / name
        files = sorted(p for p in folder.rglob("*") if p.is_file())
        assert {"/state/" + p.relative_to(state).as_posix() for p in files} == set(report["files"])
        assert report["source_hashes"]
        if denied:
            verify_denied(files)
            logs = "\n".join(
                p.read_text(encoding="utf-8-sig") for p in files if p.suffix.lower() == ".log"
            )
            error = (report["error"] or "") + logs
            assert any(word in error for word in ("403", "Forbidden", "AccessDenied", "denied")), (
                f"{label}/{mode}: no actual access denial"
            )
        else:
            assert report["error"] is None, f"{label}/{mode}: {report['error']}"
            if label == "hawk_rules":
                assert report["hidden_check"] is True
            results[label]["records_verified"] += verify_artifacts(
                label, files, evidence["expected"]
            )
        item = results[label]
        item["cases"].append({"mode": mode, "denied": denied, "passed": True})
        for path, digest in report["source_hashes"].items():
            relative = path.removeprefix("/vendor/")
            assert digest == sha(ROOT / "work/vendor" / relative)
            item["source_hashes"][relative] = digest
        for path in files:
            text = path.read_text(encoding="utf-8-sig")
            for filename in ("secret.json", "negative-secret.json"):
                secret = json.loads((state / filename).read_text())["secret"]
                assert secret not in text, "Credential disclosure in collector artifact"
            item["output_hashes"][path.relative_to(RUNTIME).as_posix()] = sha(path)
    assert len(evidence["cases"]) == 4 * len(FUNCTIONS)
    for item in results.values():
        validate_cases(item["cases"])
        assert item["records_verified"] > 0 and item["output_hashes"]
        item["passed"] = True
    validate_collector_hashes(results, ROOT)
    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-existing", action="store_true")
    args = parser.parse_args()
    evidence = (
        json.loads((ROOT / "work/mail-collector-run.json").read_text())
        if args.verify_existing
        else None
    )
    report = qualify_collectors(evidence)
    (ROOT / "work/mail-collector-results.json").write_text(json.dumps(report, indent=2))
    print("All original mail collector output/denial cases passed")
