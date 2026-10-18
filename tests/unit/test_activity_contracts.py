from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mockazure.activity.contracts import (
    CONTENT_TYPES,
    content_type,
    from_graph,
    validate_record,
    window,
)
from mockazure.endpoints import EndpointProfile, resolve_resource


def test_activity_audience_and_old_profile_defaults():
    profile = EndpointProfile(login="https://localhost:28443", graph="https://localhost:28443")
    assert profile.activity == "https://localhost:28443"
    assert resolve_resource("https://manage.office.com", profile).service == "activity"
    assert resolve_resource("https://graph.microsoft.com", profile).service == "graph"
    with pytest.raises(ValueError):
        resolve_resource("https://localhost:28443", profile)


def test_common_schema_and_original_identity():
    tenant, identifier = uuid4(), uuid4()
    graph = {
        "id": str(identifier),
        "organizationId": str(tenant),
        "createdDateTime": "2026-10-17T00:00:00Z",
        "auditLogRecordType": "ExchangeAdmin",
        "userType": "ServicePrincipal",
        "userId": str(uuid4()),
        "operation": "Set-Mailbox",
        "service": "Exchange",
        "auditData": {"dynamicProperties": {"Operation": "wrong", "Extra": "keep"}},
    }
    record = from_graph(graph, tenant)
    assert record["Id"] == str(identifier)
    assert record["RecordType"] == 1 and record["UserType"] == 6
    assert record["Operation"] == "Set-Mailbox" and record["Extra"] == "keep"
    assert "auditData" not in record
    assert validate_record(record, tenant) == record
    assert content_type(record) == "Audit.Exchange"
    for invalid in (
        {**record, "OrganizationId": str(uuid4())},
        {**record, "RecordType": True},
        {**record, "UserKey": None},
        {**record, "ClientSecret": "bad"},
    ):
        with pytest.raises(ValueError):
            validate_record(invalid, tenant)
    assert len(CONTENT_TYPES) == 5


def test_publication_window_bounds():
    now = datetime(2026, 10, 17, tzinfo=UTC)
    assert window(None, None, now) == (now - timedelta(days=1), now)
    for start, end in [
        (None, now.isoformat()),
        ((now - timedelta(days=8)).isoformat(), now.isoformat()),
        ((now - timedelta(days=2)).isoformat(), now.isoformat()),
        (now.isoformat(), now.isoformat()),
    ]:
        with pytest.raises(ValueError):
            window(start, end, now)
