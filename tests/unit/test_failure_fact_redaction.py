from datetime import UTC, datetime
from uuid import uuid4

import pytest


def failure(**changes):
    from mockazure.evidence.failure_models import FailureFact

    return FailureFact(
        **{
            "tenant_id": uuid4(),
            "source_resource_id": (
                f"/subscriptions/{uuid4()}/resourceGroups/lab/"
                "providers/Microsoft.Storage/storageAccounts/account"
            ),
            "service": "blob",
            "operation_key": "GetBlob",
            "request_id": uuid4(),
            "occurred_at": datetime.now(UTC),
            "principal_id": uuid4(),
            "status_code": 403,
            "error_code": "AuthorizationPermissionMismatch",
            "safe_properties": {"authenticationType": "oauth"},
            **changes,
        }
    )


@pytest.mark.parametrize(
    "properties",
    [
        {"secret": "never-export"},
        {"authorization": "Bearer token"},
        {"url": "https://local/blob?sig=secret"},
        {"body": {"value": "secret"}},
        {"authenticationType": "oauth", "unknown": "value"},
        {"authenticationType": "Bearer actual-token"},
    ],
)
def test_secret_and_sas_material_never_appears_in_diagnostics(properties):
    with pytest.raises(ValueError):
        failure(safe_properties=properties)


def test_failure_facts_validate_source_and_operation():
    for changes in [
        {"source_resource_id": "https://real.example/secrets"},
        {"operation_key": "inventedOperation"},
        {"service": "keyvault"},
        {"status_code": 200},
        {"error_code": "secret\nvalue"},
    ]:
        with pytest.raises(ValueError):
            failure(**changes)


def test_failure_delivery_uses_actual_outcome():
    from mockazure.monitor.publication import archive_record, table_row

    value = failure()
    event = {
        "id": str(value.request_id),
        "service": "blob",
        "resource_id": value.source_resource_id + "/blobServices/default",
        "operation": value.operation_key,
        "category": "StorageRead",
        "occurred_at": value.occurred_at.isoformat(),
        "payload": {
            "outcome": "Failed",
            "statusCode": 403,
            "errorCode": value.error_code,
            "authenticationType": "oauth",
        },
    }
    table, row = table_row(event, "Dedicated")
    assert table == "StorageBlobLogs"
    assert row["StatusCode"] == "403"
    assert row["StatusText"] == value.error_code
    archived = archive_record(event)
    assert archived["resultType"] == "Failed"
    assert archived["resultSignature"] == "403"
    assert archived["level"] == "Error"


def test_file_range_operation_uses_the_documented_rest_name():
    from types import SimpleNamespace

    from mockazure.evidence.failures import storage_operation

    request = SimpleNamespace(method="GET", query_params={"comp": "rangelist"})
    assert storage_operation(request, "file", "read_file") == "ListRanges"
