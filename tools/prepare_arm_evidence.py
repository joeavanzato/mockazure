"""Deterministic exact-time ARM evidence with an independent expected-output manifest."""

import json
import ssl
import time
from datetime import UTC, datetime
from uuid import UUID, uuid5

import httpx

from tools.prepare_arm import RUNTIME, save

NAMESPACE = UUID("55860348-84f3-49a0-a36c-55c6761c7578")


def prepare():
    state = json.loads((RUNTIME / "provisioning.json").read_text(encoding="utf-8"))
    tenant, subscription = state["tenant_id"], state["subscription_id"]
    scope = state["resource_ids"][0]
    # Calculate fixture ticks independently from the production Activity implementation.
    midnight = datetime(2026, 10, 7, tzinfo=UTC)
    seconds = (midnight - datetime(1, 1, 1, tzinfo=UTC)).days * 86400
    records = []
    for number in range(1003):
        fractional = 1234568 if number == 1002 else 1234567
        stamp = f"2026-10-07T00:00:00.{fractional:07d}Z"
        ticks = seconds * 10_000_000 + fractional
        identifier = str(uuid5(NAMESPACE, f"{subscription}:event:{number}"))
        correlation = str(uuid5(NAMESPACE, f"{subscription}:correlation"))
        payload = {
            "id": f"{scope}/events/{identifier}/ticks/{ticks}",
            "eventDataId": identifier,
            "eventTimestamp": stamp,
            "submissionTimestamp": stamp,
            "subscriptionId": subscription,
            "tenantId": tenant,
            "resourceId": scope,
            "resourceGroupName": "evidence",
            "resourceProviderName": {"value": "Microsoft.Compute"},
            "resourceType": {"value": "Microsoft.Compute/virtualMachines"},
            "operationName": {"value": "Microsoft.Compute/virtualMachines/write"},
            "eventName": {"value": "EndRequest"},
            "correlationId": correlation,
            "operationId": correlation,
            "status": {"value": "Succeeded"},
            "category": {"value": "Administrative"},
            "level": "Informational",
            "caller": "historical-actor@arm.test",
            "authorization": {
                "action": "Microsoft.Compute/virtualMachines/write",
                "scope": f"/subscriptions/{subscription}",
            },
            "properties": {"fixtureOrdinal": str(number)},
        }
        records.append(
            {
                "family": "activityLog",
                "api_version": "2015-04-01",
                "payload": payload,
                "occurred_at": stamp,
                "available_at": stamp,
            }
        )
    expected = [r["payload"] for r in records]
    save(RUNTIME / "arm-expected.json", expected)
    fixture = {
        "start": "2026-10-07T00:00:00Z",
        "end": "2026-10-07T23:59:59Z",
        "record_ids": [r["eventDataId"] for r in expected],
        "count": len(expected),
        "group": "evidence",
        "resource": scope,
        "correlation_id": expected[0]["correlationId"],
    }
    save(RUNTIME / "arm-fixture.json", fixture)
    with httpx.Client(
        base_url="https://localhost:36443",
        trust_env=False,
        timeout=120,
        verify=ssl.create_default_context(cafile=str(RUNTIME / "instance/ca.pem")),
        headers={
            "Authorization": "Bearer "
            + (RUNTIME / "instance/admin.token").read_text(encoding="utf-8").strip()
        },
    ) as client:
        base = f"/_mock/tenants/{tenant}/evidence"
        changed = client.patch(
            base + "/settings",
            json={"clock_mode": "fixed", "fixed_time": "2026-10-08T00:00:00Z", "delay_seconds": 0},
        )
        assert changed.status_code == 200, changed.status_code
        for offset in range(0, len(records), 500):
            result = client.post(
                base + "/import",
                json={
                    "schema_version": 1,
                    "provenance": {"source": "M5 immutable qualification fixture"},
                    "records": records[offset : offset + 500],
                },
            )
            assert result.status_code == 200, result.status_code
        until = time.monotonic() + 120
        while time.monotonic() < until:
            response = client.get(base + "/export", params={"family": "activityLog"})
            assert response.status_code == 200
            if {r["payload"]["eventDataId"] for r in response.json()["records"]} >= set(
                fixture["record_ids"]
            ):
                break
            time.sleep(0.5)
        else:
            raise AssertionError("Activity fixture did not publish")
    for language in ("native", "python", "powershell"):
        folder = RUNTIME / "clients" / f"{language}-state"
        path = folder / "client.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["arm_fixture"] = fixture
        save(path, config)
        save(folder / "arm-expected.json", expected)
    print("M5 published1003 deterministic Activity records with exact expected IDs/timestamps")


if __name__ == "__main__":
    prepare()
