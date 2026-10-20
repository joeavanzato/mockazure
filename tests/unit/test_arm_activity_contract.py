import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from mockazure.evidence.schemas import SourceEnvelope, SourceRef
from mockazure.providers.arm.contracts import DATA, document, manifest, validate_arm_payload

TENANT = UUID("22222222-2222-2222-2222-222222222222")
SUB = UUID("33333333-3333-3333-3333-333333333333")
SCOPE = f"/subscriptions/{SUB}/resourceGroups/evidence"


def event_payload(identifier=None, when="2026-10-01T01:02:03.1234567Z", scope=SCOPE):
    from mockazure.providers.arm.activity import instant

    identifier = identifier or uuid4()
    _, ticks = instant(when)
    return {
        "id": f"{scope}/events/{identifier}/ticks/{ticks}",
        "eventDataId": str(identifier),
        "eventTimestamp": when,
        "submissionTimestamp": when,
        "resourceId": scope,
        "subscriptionId": str(SUB),
        "tenantId": str(TENANT),
        "resourceGroupName": "evidence",
        "resourceProviderName": {"value": "Microsoft.Resources"},
        "operationName": {"value": "Microsoft.Resources/subscriptions/resourceGroups/write"},
        "status": {"value": "Succeeded"},
        "category": {"value": "Administrative"},
        "level": "Informational",
        "correlationId": str(uuid4()),
    }


def test_official_activity_example_keeps_100ns_and_rbac_id():
    from mockazure.providers.arm.activity import instant, validate_activity_payload

    source = (
        manifest()["roots"]["activity"].rsplit("/", 1)[0] + "/examples/GetActivityLogsFiltered.json"
    )
    payload = document(source)["responses"]["200"]["body"]["value"][0]
    assert "resourceId" not in payload  # real official example derives scope from ARM event ID
    validate_activity_payload(payload)
    floor, ticks = instant(payload["eventTimestamp"])
    assert ticks == 635574752669792776
    assert floor.microsecond == 979277
    assert instant("2015-01-21T23:14:26.9792776+01:00")[1] == ticks
    assert instant("2015-01-21T22:14:26.9792777Z")[1] == ticks + 1
    assert json.loads((DATA / manifest()["files"][source]["path"]).read_text(encoding="utf-8"))


def test_native_arm_projection_is_captured_schema_valid_and_not_fabricated():
    from mockazure.providers.arm.activity import project_arm, validate_activity_payload

    now = datetime(2026, 10, 1, 1, 2, 3, 123456, tzinfo=UTC)
    correlation = uuid4()
    source = SourceEnvelope(
        ref=SourceRef(tenant_id=TENANT, kind="arm", id=uuid4()),
        occurred_at=now,
        available_at=now,
        source_revision=1,
        generation=uuid4(),
        facts={
            "operation": "Microsoft.Resources/subscriptions/resourceGroups/write",
            "scope": SCOPE,
            "actor": {
                "user": {
                    "id": str(uuid4()),
                    "displayName": "Analyst",
                    "userPrincipalName": "analyst@local.test",
                }
            },
            "before": {},
            "after": {"name": "evidence", "location": "eastus"},
            "correlation_id": str(correlation),
            "provenance": "native",
            "status": "Succeeded",
        },
    )
    batch = project_arm(source)
    assert batch == project_arm(source)
    item = batch.items[0]
    assert item.family == "activityLog"
    payload = item.payload
    validate_arm_payload("EventData", payload)
    validate_activity_payload(payload, tenant=TENANT, occurred_at=now, available_at=now)
    assert item.id == UUID(payload["eventDataId"])
    assert payload["id"].startswith(SCOPE + "/events/")
    assert payload["authorization"] == {"action": source.facts["operation"], "scope": SCOPE}
    assert payload["caller"] == "analyst@local.test"
    assert payload["correlationId"] == str(correlation)
    assert payload["status"]["value"] == "Succeeded"
    assert "httpRequest" not in payload and "claims" not in payload


def test_activity_rejects_identity_time_scope_and_secret_conflicts():
    from mockazure.providers.arm.activity import validate_activity_payload

    payload = event_payload()
    for bad in (
        payload | {"eventDataId": str(uuid4())},
        payload | {"eventTimestamp": "2026-10-01T01:02:03.1234568Z"},
        payload | {"subscriptionId": str(uuid4())},
        payload | {"resourceId": SCOPE + "other"},
        payload | {"resourceGroupName": "elsewhere"},
        payload | {"authorization": {"scope": SCOPE + "other"}},
        payload | {"claims": {"accessToken": "forbidden"}},
        payload | {"properties": {"password": "forbidden"}},
        payload | {"submissionTimestamp": "2026-10-01T01:02:03.1234566Z"},
        payload | {"tenantId": str(uuid4())},
    ):
        with pytest.raises(ValueError):
            validate_activity_payload(bad, tenant=TENANT)
    with pytest.raises(ValueError):
        validate_activity_payload(payload, occurred_at=datetime(2026, 10, 2, tzinfo=UTC))


def test_provider_registration_activity_keeps_provider_target_and_subscription_authority():
    from mockazure.providers.arm.activity import activity_dimensions, project_arm
    from mockazure.providers.arm.scopes import parse_scope

    scope = f"/subscriptions/{SUB}"
    target = scope + "/providers/Microsoft.Storage"
    now = datetime(2026, 10, 20, tzinfo=UTC)
    source = SourceEnvelope(
        ref=SourceRef(tenant_id=TENANT, kind="arm", id=uuid4()),
        occurred_at=now,
        available_at=now,
        source_revision=1,
        generation=uuid4(),
        facts={
            "operation": "Microsoft.Storage/register/action",
            "scope": scope,
            "actor": {"app": {"appId": str(uuid4())}},
            "before": {},
            "after": {"id": target, "registrationState": "Registered"},
            "correlation_id": str(uuid4()),
            "provenance": "native",
            "status": "Succeeded",
        },
    )
    payload = project_arm(source).items[0].payload
    assert payload["resourceId"] == target
    assert payload["resourceType"]["value"] == "Microsoft.Resources/providers"
    assert payload["resourceProviderName"]["value"] == "Microsoft.Storage"
    assert activity_dimensions(payload)["arm_scope_key"] == target.lower()
    assert payload["authorization"]["scope"] == scope
    # Evidence parsing must not broaden normal ARM resource/authorization inputs.
    with pytest.raises(ValueError):
        parse_scope(target, allow_subscription_resource=True)
    bad = source.model_copy(
        update={
            "facts": {
                **source.facts,
                "after": {"id": f"/subscriptions/{uuid4()}/providers/Microsoft.Storage"},
            }
        }
    )
    with pytest.raises(ValueError, match="escaped"):
        project_arm(bad)


def test_activity_historical_envelope_uses_event_data_id():
    from mockazure.evidence.datasets import EvidenceRecord

    payload = event_payload()
    record = EvidenceRecord(
        family="activityLog",
        api_version="2015-04-01",
        payload=payload,
        occurred_at=payload["eventTimestamp"],
        available_at=payload["submissionTimestamp"],
    )
    assert record.payload == payload
    assert record.subject_id() is None
