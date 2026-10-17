import copy
import json
from pathlib import Path

import pytest


def oracle():
    assert Path("tools/qualify_mail_collectors.py").exists(), "Collector oracle missing"
    from tools.qualify_mail_collectors import validate_cases, verify_denied, verify_rows

    return verify_rows, verify_denied, validate_cases


def test_identity_pages_and_fields_fail_closed(tmp_path):
    verify, _, _ = oracle()
    expected = [{"id": str(i), "recipient": "a@example.test", "enabled": True} for i in range(1003)]
    verify(expected, expected, "id")
    for bad in (expected[:-1], expected + expected[:1]):
        with pytest.raises(AssertionError):
            verify(bad, expected, "id")
    for key, value in (("recipient", "wrong@example.test"), ("enabled", False)):
        bad = copy.deepcopy(expected)
        bad[0][key] = value
        with pytest.raises(AssertionError):
            verify(bad, expected, "id")


def test_denied_outputs_and_malformed_json(tmp_path):
    _, verify, _ = oracle()
    path = tmp_path / "output.json"
    path.write_text("[]")
    verify([path])
    for content in ('[{"id":"secret"}]', "[] []"):
        path.write_text(content)
        with pytest.raises((AssertionError, json.JSONDecodeError)):
            verify([path])
    path.unlink()
    text = tmp_path / "Mailbox_Info.txt"
    text.write_text("UserPrincipalName : private@example.test")
    with pytest.raises(AssertionError):
        verify([text])


def test_cases_require_literal_success_and_all_modes():
    _, _, verify = oracle()
    cases = [
        {"mode": mode, "denied": denied, "passed": True}
        for mode in ("application", "delegated")
        for denied in (False, True)
    ]
    verify(cases)
    for bad in (cases[:-1], cases + cases[:1]):
        with pytest.raises(AssertionError):
            verify(bad)
    for value in (1, "true", False, None):
        bad = copy.deepcopy(cases)
        bad[0]["passed"] = value
        with pytest.raises(AssertionError):
            verify(bad)


def test_trace_artifact_rejects_truncation_and_recipient_corruption(tmp_path):
    import csv

    from tools.qualify_mail_collectors import verify_artifacts

    expected = {
        "mailbox": {"UserPrincipalName": "mail@example.test"},
        "fixture": {"time": "2026-10-16T07:51:42+00:00", "trace_ids": ["a", "b", "c"]},
    }
    records = [
        {
            "MessageTraceId": identity,
            "Received": f"2026-10-16T07:51:42.{i:06d}+00:00",
            "SenderAddress": "sender@example.test",
            "RecipientAddress": "mail@example.test",
            "Status": "Delivered",
            "Size": 1024 + i,
            "MessageId": f"<qualified-{i}@example.test>",
            "Subject": f"Qualified trace {i}",
            "FromIP": "192.0.2.42",
            "ToIP": "192.0.2.43",
        }
        for i, identity in enumerate(expected["fixture"]["trace_ids"])
    ]
    path = tmp_path / "trace.csv"

    def save(values):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=records[0])
            writer.writeheader()
            writer.writerows(values)

    save(records)
    assert verify_artifacts("mes_trace", [path], expected) == 3
    for bad in (records[:-1], records + records[:1]):
        save(bad)
        with pytest.raises(AssertionError):
            verify_artifacts("mes_trace", [path], expected)
    for value in ("", "2026-10-16T07:51:42+00:00", "2026-10-16T08:51:42.000002+00:00"):
        bad = copy.deepcopy(records)
        bad[-1]["Received"] = value
        save(bad)
        with pytest.raises((AssertionError, ValueError)):
            verify_artifacts("mes_trace", [path], expected)
    bad = copy.deepcopy(records)
    bad[-1]["RecipientAddress"] = "other@example.test"
    save(bad)
    with pytest.raises(AssertionError):
        verify_artifacts("mes_trace", [path], expected)


def test_audit_artifact_rejects_wrong_identity_and_malformed_output(tmp_path):
    from tools.qualify_mail_collectors import verify_artifacts

    expected = {
        "mailbox": {"UserPrincipalName": "mail@example.test"},
        "fixture": {"time": "2026-10-16T07:51:42+00:00", "audit_ids": ["a", "b"]},
    }
    records = [
        {
            "id": identity,
            "createdDateTime": "2026-10-16T07:51:42+00:00",
            "operation": "MailItemsAccessed",
            "service": "Exchange",
            "auditLogRecordType": "ExchangeItem",
            "userPrincipalName": "mail@example.test",
            "objectId": f"qualified-message-{i}",
            "clientIp": "192.0.2.42",
            "auditData": {
                "dynamicProperties": {
                    "Id": identity,
                    "CreationTime": "2026-10-16T07:51:42+00:00",
                    "Operation": "MailItemsAccessed",
                    "ObjectId": f"qualified-message-{i}",
                    "UserId": "mail@example.test",
                    "RecordType": 2,
                    "ResultStatus": "Succeeded",
                }
            },
        }
        for i, identity in enumerate(expected["fixture"]["audit_ids"])
    ]
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(records))
    assert verify_artifacts("mes_audit", [path], expected) == 2
    for field in ("createdDateTime", "CreationTime"):
        for value in ("", "2026-10-16T07:51:42.000001+00:00", "2026-10-16T08:51:42+00:00"):
            bad = copy.deepcopy(records)
            target = (
                bad[-1] if field == "createdDateTime" else bad[-1]["auditData"]["dynamicProperties"]
            )
            target[field] = value
            path.write_text(json.dumps(bad))
            with pytest.raises((AssertionError, ValueError)):
                verify_artifacts("mes_audit", [path], expected)
    bad = copy.deepcopy(records)
    bad[-1]["id"] = "wrong"
    path.write_text(json.dumps(bad))
    with pytest.raises(AssertionError):
        verify_artifacts("mes_audit", [path], expected)
    path.write_text(json.dumps(records) + json.dumps(records))
    with pytest.raises(json.JSONDecodeError):
        verify_artifacts("mes_audit", [path], expected)


def test_collector_hashes_bind_existing_sources_and_outputs(tmp_path):
    import hashlib

    from tools.qualify_mail_collectors import validate_collector_hashes

    source = tmp_path / "work/vendor/collectors/original.ps1"
    output = tmp_path / ".runtime/m4/clients/powershell-state/output.csv"
    for path in (source, output):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("verified")
    digest = hashlib.sha256(b"verified").hexdigest()
    result = {
        "sample": {
            "source_hashes": {"collectors/original.ps1": digest},
            "output_hashes": {"clients/powershell-state/output.csv": digest},
        }
    }
    validate_collector_hashes(result, tmp_path)
    output.write_text("tampered")
    with pytest.raises(AssertionError):
        validate_collector_hashes(result, tmp_path)
    output.write_text("verified")
    for field in ("source_hashes", "output_hashes"):
        bad = copy.deepcopy(result)
        bad["sample"][field] = {"../outside": digest}
        with pytest.raises(AssertionError):
            validate_collector_hashes(bad, tmp_path)


def test_native_audit_export_preserves_timestamp_value_not_decimal_padding(tmp_path):
    from tools.qualify_mail_collectors import verify_artifacts

    record = {
        "id": "native",
        "operation": "Set-Mailbox",
        "userPrincipalName": None,
        "userId": "app",
        "objectId": "mail@example.test",
        "createdDateTime": "2026-10-17T08:26:18.497870+00:00",
        "auditData": {
            "dynamicProperties": {
                "Id": "native",
                "Operation": "Set-Mailbox",
                "ObjectId": "mail@example.test",
                "UserId": "app",
                "RecordType": 1,
                "ResultStatus": "True",
                "CreationTime": "2026-10-17T08:26:18.497870+00:00",
            }
        },
    }
    expected = {
        "mailbox": {"UserPrincipalName": "mail@example.test"},
        "fixture": {"audit_ids": []},
        "native": [record],
    }
    actual = copy.deepcopy(record)
    actual["createdDateTime"] = "2026-10-17T08:26:18.49787+00:00"
    actual["auditData"]["dynamicProperties"]["CreationTime"] = actual["createdDateTime"]
    path = tmp_path / "native.json"
    path.write_text(json.dumps([actual]))
    assert verify_artifacts("mes_audit_native", [path], expected) == 1
    actual["createdDateTime"] = "2026-10-17T08:26:18.497871+00:00"
    path.write_text(json.dumps([actual]))
    with pytest.raises(AssertionError):
        verify_artifacts("mes_audit_native", [path], expected)


@pytest.mark.parametrize("label", ["mes_audit_jsonl", "mes_audit_sofelk", "mes_audit_csv"])
def test_all_audit_formats_require_exact_historical_time(tmp_path, label):
    import csv

    from tools.qualify_mail_collectors import verify_artifacts

    when = "2026-10-16T07:51:42+00:00"
    expected = {
        "mailbox": {"UserPrincipalName": "mail@example.test"},
        "fixture": {"time": when, "audit_ids": ["a"]},
    }
    common = {
        "Id": "a",
        "Operation": "MailItemsAccessed",
        "ObjectId": "qualified-message-0",
        "UserId": "mail@example.test",
        "Workload": "Exchange",
        "ResultStatus": "Succeeded",
        "RecordType": 2,
        "CreationTime": when,
    }
    record = {
        "id": "a",
        "operation": "MailItemsAccessed",
        "service": "Exchange",
        "auditLogRecordType": "ExchangeItem",
        "userPrincipalName": "mail@example.test",
        "objectId": "qualified-message-0",
        "clientIp": "192.0.2.42",
        "createdDateTime": when,
    }
    path = tmp_path / ("audit.csv" if label.endswith("csv") else "audit.jsonl")

    def save(value, outer=when):
        if label.endswith("csv"):
            row = record | {
                "createdDateTime": outer,
                "auditData": json.dumps({"dynamicProperties": value}),
            }
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=row)
                writer.writeheader()
                writer.writerow(row)
        else:
            path.write_text(json.dumps({"dynamicProperties": value}) + "\n")

    save(common)
    assert verify_artifacts(label, [path], expected) == 1
    for value in (None, "", "2026-10-16T07:51:42.000001+00:00", "2026-10-16T08:51:42+00:00"):
        bad = dict(common)
        if value is None:
            bad.pop("CreationTime")
        else:
            bad["CreationTime"] = value
        save(bad)
        with pytest.raises((AssertionError, ValueError, KeyError)):
            verify_artifacts(label, [path], expected)
    if label.endswith("csv"):
        save(common, "10/16/2026 7:51:42\u202fAM")
        assert verify_artifacts(label, [path], expected) == 1
        save(common, "2026-10-16T07:51:42.000001+00:00")
        with pytest.raises(AssertionError):
            verify_artifacts(label, [path], expected)
