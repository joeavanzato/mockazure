"""Idempotent M4-only mailbox, UAL and trace qualification data."""

import json
import ssl
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

import httpx

from tools.prepare_mail import PROFILE, RUNTIME, save

NAMESPACE = UUID("5ec17a09-e799-442b-a9e5-268ad1f296af")


def checked(response, status=200):
    assert response.status_code == status, (
        f"Local fixture request failed: {response.status_code} {response.request.url.path}"
    )
    return response.json()


def prepare():
    state = json.loads((RUNTIME / "provisioning.json").read_text())
    path = RUNTIME / "mail-fixture.json"
    fixture = (
        json.loads(path.read_text())
        if path.exists()
        else {
            "time": (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0).isoformat(),
            "rule_ids": [],
            "audit_ids": [],
            "trace_ids": [],
        }
    )
    save(path, fixture)
    when = datetime.fromisoformat(fixture["time"])
    base = f"/_mock/tenants/{state['tenant_id']}"
    with httpx.Client(
        base_url=PROFILE.text("https://localhost:34443"),
        trust_env=False,
        timeout=60,
        verify=ssl.create_default_context(cafile=str(RUNTIME / "instance/ca.pem")),
        headers={
            "Authorization": "Bearer " + (RUNTIME / "instance/admin.token").read_text().strip()
        },
    ) as client:
        checked(client.put(base + f"/mail/audit-roles/{state['user_id']}/View-Only Audit Logs"))
        checked(client.patch(base + "/evidence/settings", json={"delay_seconds": 0}))
        if "exchange_public_client_id" not in fixture:
            app = checked(
                client.post(
                    base + "/identity/applications",
                    json={
                        "display_name": "M4 delegated Exchange qualification",
                        "client_mode": "public",
                        "redirect_uris": ["http://localhost:8123/callback"],
                        "required_permissions": [
                            {
                                "resource": "exchange",
                                "kind": "delegated",
                                "permissions": ["Exchange.Manage"],
                            }
                        ],
                    },
                ),
                201,
            )
            fixture["exchange_public_client_id"] = app["client_id"]
            save(path, fixture)
        checked(
            client.put(
                base + f"/identity/grants/{uuid5(NAMESPACE, 'exo-delegated-grant')}",
                json={
                    "client_id": fixture["exchange_public_client_id"],
                    "resource": "exchange",
                    "kind": "delegated",
                    "user_id": state["user_id"],
                    "permissions": ["Exchange.Manage"],
                },
            )
        )
        while len(fixture["rule_ids"]) < 3:
            index = len(fixture["rule_ids"])
            rule = checked(
                client.post(
                    base + f"/mail/mailboxes/{state['user_id']}/rules",
                    json={
                        "rule": {
                            "displayName": f"Qualified rule {index}",
                            "sequence": index + 1,
                            "isEnabled": True,
                            "actions": {
                                "forwardTo": [
                                    {"emailAddress": {"address": f"target{index}@example.test"}}
                                ],
                                "stopProcessingRules": index == 2,
                            },
                        },
                    },
                ),
                201,
            )
            fixture["rule_ids"].append(rule["id"])
            save(path, fixture)
        records = []
        for index in range(1003):
            identifier = str(uuid5(NAMESPACE, "audit-" + str(index)))
            payload = {
                "id": identifier,
                "createdDateTime": when.isoformat(),
                "organizationId": state["tenant_id"],
                "service": "Exchange",
                "operation": "MailItemsAccessed",
                "auditLogRecordType": "ExchangeItem",
                "userId": state["user_id"],
                "userPrincipalName": state["user"]["username"],
                "userType": "Regular",
                "objectId": f"qualified-message-{index}",
                "clientIp": "192.0.2.42",
                "auditData": {
                    "dynamicProperties": {
                        "Id": identifier,
                        "CreationTime": when.isoformat(),
                        "Operation": "MailItemsAccessed",
                        "OrganizationId": state["tenant_id"],
                        "Workload": "Exchange",
                        "UserId": state["user"]["username"],
                        "ObjectId": f"qualified-message-{index}",
                        "RecordType": 2,
                        "UserType": 0,
                        "UserKey": state["user_id"],
                        "ResultStatus": "Succeeded",
                    }
                },
            }
            records.append(
                {
                    "family": "auditLogRecord",
                    "payload": payload,
                    "occurred_at": when.isoformat(),
                    "available_at": when.isoformat(),
                }
            )
        for offset in range(0, len(records), 500):
            checked(
                client.post(
                    base + "/evidence/import",
                    json={
                        "schema_version": 1,
                        "provenance": {"source": "M4-original-client-qualification"},
                        "records": records[offset : offset + 500],
                    },
                )
            )
        fixture.update(
            audit_ids=[r["payload"]["id"] for r in records],
            audit_object="qualified-message-0",
            audit_start=(when - timedelta(seconds=1)).isoformat(),
            audit_end=(when + timedelta(seconds=1)).isoformat(),
        )
        trace = []
        for index in range(5003):
            identifier = str(uuid5(NAMESPACE, "trace-" + str(index)))
            received = when + timedelta(microseconds=index)
            trace.append(
                {
                    "message_trace_id": identifier,
                    "received": received.isoformat(),
                    "sender_address": "sender@example.test",
                    "recipient_address": state["user"]["username"],
                    "mailbox_user_id": state["user_id"],
                    "message_id": f"<qualified-{index}@example.test>",
                    "subject": f"Qualified trace {index}",
                    "status": "Delivered",
                    "size": 1024 + index,
                    "from_ip": "192.0.2.42",
                    "to_ip": "192.0.2.43",
                    "details": [
                        {
                            "date": received.isoformat(),
                            "event": "DELIVER",
                            "action": "Delivered",
                            "detail": "Delivered to the synthetic mailbox",
                            "data": "",
                        }
                    ],
                }
            )
        for offset in range(0, len(trace), 1000):
            checked(
                client.post(
                    base + "/mail/traces",
                    json={
                        "source": "M4-original-client-qualification",
                        "records": trace[offset : offset + 1000],
                    },
                ),
                201,
            )
        fixture.update(
            trace_ids=[r["message_trace_id"] for r in trace],
            trace_start=(when - timedelta(seconds=1)).isoformat(),
            trace_end=(when + timedelta(seconds=1)).isoformat(),
        )
        save(path, fixture)
    for language in ("native", "python", "powershell"):
        config_path = RUNTIME / f"clients/{language}-state/client.json"
        config = json.loads(config_path.read_text())
        config["mail_fixture"] = fixture
        config["exchange_public_client_id"] = fixture["exchange_public_client_id"]
        config["output_file"] = (
            str(config_path.parent / "graph-mail.json")
            if language == "native"
            else "/state/graph-mail.json"
        )
        save(config_path, config)
    print("M4 fixture persisted:3 rules,1003 UAL records,5003 trace rows")


if __name__ == "__main__":
    prepare()
