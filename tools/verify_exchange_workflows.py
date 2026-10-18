"""Qualify durable UAL through the original Exchange module in an isolated local lab."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tools.verify_default import main as smoke
from tools.verify_exchange_collections import qualify as qualify_collections


def prepare(request, base, tenant, state):
    # The pinned minimal PowerShell image has no IANA timezone database.
    # Supply a validated fixed-offset TZif fixture without changing the host.
    import io
    import struct
    from zoneinfo import ZoneInfo

    tzif = b"TZif" + b"\0" * 16 + struct.pack(">6i", 0, 0, 0, 0, 1, 4)
    tzif += struct.pack(">iBB", -18000, 0, 0) + b"TST\0"
    zone = ZoneInfo.from_file(io.BytesIO(tzif))
    assert datetime.now(zone).utcoffset() == timedelta(hours=-5)
    (state / "test-timezone").write_bytes(tzif)
    when = datetime.now(UTC) - timedelta(hours=1)
    records = []
    for index in range(1101):
        identifier = str(uuid4())
        records.append(
            {
                "family": "auditLogRecord",
                "occurred_at": when.isoformat(),
                "available_at": when.isoformat(),
                "payload": {
                    "id": identifier,
                    "organizationId": tenant["id"],
                    "createdDateTime": when.isoformat(),
                    "service": "Exchange",
                    "operation": "Set-Mailbox",
                    "auditLogRecordType": "ExchangeAdmin",
                    "userPrincipalName": "actor@" + tenant["domain"],
                    "userType": "Regular",
                    "objectId": f"workflow-mailbox-{index}",
                    "clientIp": "192.0.2.42",
                    "auditData": {"dynamicProperties": {"Marker": f"workflow-{index}"}},
                },
            }
        )
    result = request(
        "POST",
        base + "/evidence/import",
        {
            "schema_version": 1,
            "provenance": {"source": "Exchange native workflow qualification"},
            "records": records,
        },
    )
    assert result["inserted"] == 1101
    for _ in range(60):
        drained = request("POST", base + "/evidence/drain", {"max_jobs": 50})
        assert drained["failed"] == 0, drained
        if drained["pending"] == 0 and drained["processed"] == 0:
            break
    else:
        raise AssertionError("Local audit projection did not drain")
    (state / "audit-expected.json").write_text(
        json.dumps(
            {
                "ids": [record["payload"]["id"] for record in records],
                "start": (when - timedelta(minutes=1)).isoformat(),
                "end": (when + timedelta(minutes=1)).isoformat(),
            }
        )
    )


def qualify(*args):
    qualify_collections(*args, workflows=True)


if __name__ == "__main__":
    smoke(qualify=qualify)
