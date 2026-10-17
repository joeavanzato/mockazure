import pytest

from mockazure.evidence.datasets import EvidenceDataset
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_evidence_directory import drain
from tests.integration.test_graph_mutations import WRITE, user_body


async def test_native_create_delete_audit_export_is_complete_json(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        base = f"/_mock/tenants/{tenant}/evidence"
        assert (
            await client.patch(base + "/settings", json={"delay_seconds": 0})
        ).status_code == 200
        headers = await headers_for(database, settings, tenant, app, None, WRITE)
        response = await client.post(
            "/v1.0/users", headers=headers, json=user_body("export-lifecycle")
        )
        assert response.status_code == 201, response.text
        identifier = response.json()["id"]
        assert (
            await client.delete("/v1.0/users/" + identifier, headers=headers)
        ).status_code == 204
        await drain(database)
        response = await client.get(base + "/export?family=directoryAudit")
        assert response.status_code == 200
        exported = EvidenceDataset.model_validate(response.json())
        names = {r.payload["activityDisplayName"] for r in exported.records}
        assert {"Add user", "Hard Delete user"} <= names
        changes = [
            c
            for r in exported.records
            for t in r.payload["targetResources"]
            for c in t["modifiedProperties"]
            if c["displayName"] == "id"
        ]
        assert any(c["oldValue"] == "null" for c in changes)
        assert any(c["newValue"] == "null" for c in changes)


@pytest.mark.parametrize("side", ["oldValue", "newValue"])
def test_audit_absence_is_distinct_from_null_resource_property(side):
    from datetime import UTC, datetime
    from uuid import uuid4

    from mockazure.evidence.datasets import EvidenceRecord
    from mockazure.providers.graph.schema_reference import validate_payload

    now = datetime.now(UTC)
    payload = {
        "id": str(uuid4()),
        "activityDateTime": now.isoformat(),
        "activityDisplayName": "Hard Delete user",
        "category": "UserManagement",
        "initiatedBy": {},
        "targetResources": [
            {
                "id": str(uuid4()),
                "modifiedProperties": [
                    {
                        "displayName": "id",
                        "oldValue": '"prior"',
                        "newValue": '"after"',
                        side: "null",
                    }
                ],
            }
        ],
    }
    assert EvidenceRecord(
        family="directoryAudit", payload=payload, occurred_at=now, available_at=now
    )
    with pytest.raises(ValueError):
        validate_payload("v1.0", "user", {"id": None}, partial=True)
    payload["targetResources"][0]["modifiedProperties"][0][side] = "42"
    with pytest.raises(ValueError):
        EvidenceRecord(family="directoryAudit", payload=payload, occurred_at=now, available_at=now)
