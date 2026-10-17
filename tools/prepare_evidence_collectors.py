"""Deterministic, declared collector fixtures; imported through the administrative API."""

import copy
import json
import ssl
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import httpx

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime/m3b"
NS = UUID("1c21fe2c-00e6-4325-913c-b49029ce7328")


def fixtures(config):
    # Separate from the SDK's Oct 7 exact-ID window. Fixed seed preserves replay.
    when = datetime(2026, 10, 15, 12, tzinfo=UTC)
    records = []

    def add(family, payload, stamp):
        records.append(
            dict(
                family=family,
                api_version="beta" if family == "signIn" else "v1.0",
                payload=payload,
                occurred_at=stamp.isoformat(),
                available_at=stamp.isoformat(),
                origin="historical",
                entitlement_snapshot="P2",
            )
        )

    for event in ("interactiveUser", "nonInteractiveUser", "servicePrincipal", "managedIdentity"):
        for index in range(1101):
            risk = "high" if event == "interactiveUser" and index % 2 == 0 else "none"
            payload = dict(
                id=str(uuid5(NS, event + str(index))),
                createdDateTime=when.isoformat(),
                userId=config["user_id"] if event.endswith("User") else "",
                servicePrincipalId="",
                userDisplayName="SDK Admin",
                userPrincipalName="sdk-admin@graph.test" if event.endswith("User") else "",
                appId=config["client_id"],
                appDisplayName="Collector synthetic application",
                ipAddress="192.0.2.55",
                isInteractive=event == "interactiveUser",
                signInEventTypes=[event],
                status=dict(errorCode=0, failureReason=None, additionalDetails=None),
                conditionalAccessStatus="notApplied",
                riskDetail="none",
                riskState="atRisk" if risk == "high" else "none",
                riskLevelDuringSignIn=risk,
                riskLevelAggregated=risk,
                riskEventTypes_v2=[],
            )
            add("signIn", payload, when)
    for index in range(1101):
        payload = dict(
            id=str(uuid5(NS, "audit" + str(index))),
            activityDateTime=when.isoformat(),
            activityDisplayName="Update user",
            category="UserManagement",
            loggedByService="Core Directory",
            operationType="Update",
            result="success",
            resultReason=None,
            correlationId=str(uuid5(NS, "correlation" + str(index))),
            initiatedBy=dict(
                user=dict(
                    id=config["user_id"],
                    displayName="SDK Admin",
                    userPrincipalName="sdk-admin@graph.test",
                )
            ),
            targetResources=[
                dict(
                    id=config["user_id"],
                    type="User",
                    displayName="SDK Admin",
                    userPrincipalName="sdk-admin@graph.test",
                    modifiedProperties=[],
                )
            ],
            additionalDetails=[],
        )
        add("directoryAudit", payload, when)
    # An outside-window target match must survive MES's literal unparenthesized OR.
    other = copy.deepcopy(records[-1]["payload"])
    other.update(
        id=str(uuid5(NS, "audit-outside-target")),
        activityDateTime=(when - timedelta(days=2)).isoformat(),
    )
    other["initiatedBy"]["user"]["userPrincipalName"] = "unrelated@graph.test"
    add("directoryAudit", other, when - timedelta(days=2))
    # Real discriminators, not extra expected matches: other user and outside-window.
    for label, field, value in (
        ("other-user", "userPrincipalName", "distractor@graph.test"),
        ("outside-time", "createdDateTime", (when - timedelta(days=2)).isoformat()),
    ):
        payload = copy.deepcopy(records[0]["payload"])
        payload.update(id=str(uuid5(NS, label)))
        payload[field] = value
        stamp = datetime.fromisoformat(payload["createdDateTime"])
        add("signIn", payload, stamp)
    return records


def seed():
    config = json.loads((RUNTIME / "clients/native-state/client.json").read_text())
    records = fixtures(config)
    from mockazure.evidence.datasets import EvidenceDataset

    dataset = dict(
        schema_version=1,
        provenance=dict(source="original-collector-qualification", seed=42, generator="m3b-v1"),
        records=records,
    )
    EvidenceDataset.model_validate(dataset)
    base = f"/_mock/tenants/{config['tenant_id']}/evidence"
    with httpx.Client(
        base_url="https://localhost:51443",
        verify=ssl.create_default_context(cafile=str(RUNTIME / "instance/ca.pem")),
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0),
        timeout=180,
        headers={
            "Authorization": "Bearer " + (RUNTIME / "instance/admin.token").read_text().strip()
        },
    ) as client:
        for offset in range(0, len(records), 500):
            response = client.post(
                base + "/import", json={**dataset, "records": records[offset : offset + 500]}
            )
            response.raise_for_status()
        (RUNTIME / "collector-seed.json").write_text(json.dumps(dataset, indent=2))
        print("Imported collector fixtures:", len(records), flush=True)
        for _ in range(240):
            waiting = False
            for status in ("pending", "running", "failed"):
                response = client.get(base + "/jobs", params={"state": status, "limit": 1})
                response.raise_for_status()
                rows = response.json()["value"]
                if status == "failed" and rows:
                    raise AssertionError("Collector projection failed")
                waiting = waiting or bool(rows)
            if not waiting:
                print("Collector projections ready", flush=True)
                break
            time.sleep(5)
        else:
            raise AssertionError("Collector projections did not drain")
    return dataset


if __name__ == "__main__":
    seed()
