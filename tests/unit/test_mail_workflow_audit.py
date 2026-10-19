"""Native message workflow facts project to the actual Exchange item record family."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mockazure.evidence.schemas import SourceEnvelope, SourceRef
from mockazure.mail.audit import project_mail


@pytest.mark.parametrize("operation", ["Send", "SendAs", "SendOnBehalf", "Create"])
def test_workflow_operation_is_exchange_item_evidence(operation):
    now = datetime.now(UTC)
    tenant = uuid4()
    source = SourceEnvelope(
        ref=SourceRef(tenant_id=tenant, kind="mail", id=uuid4()),
        source_revision=1,
        generation=uuid4(),
        occurred_at=now,
        available_at=now,
        facts={
            "operation": operation,
            "actor": {
                "user": None,
                "app": {"servicePrincipalId": str(uuid4()), "appId": str(uuid4())},
            },
            "mailbox": {"id": str(uuid4()), "upn": "owner@contoso.test"},
            "before": {},
            "after": {"id": "item", "parentFolderId": "folder"},
            "provenance": "native",
        },
    )
    item = project_mail(source).items[0].payload
    assert item["auditLogRecordType"] == "ExchangeItem"
    assert item["auditData"]["dynamicProperties"]["RecordType"] == 2
    assert item["operation"] == operation
