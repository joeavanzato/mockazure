from datetime import UTC, datetime

import pytest

from mockazure.monitor.logs import timespan
from mockazure.monitor.tables import catalog, validate_row


@pytest.mark.parametrize(
    "field,value",
    [
        ("DurationMs", "4"),
        ("OperationName", 4),
        ("OperationCount", True),
        ("OperationCount", 2**31),
        ("DurationMs", float("nan")),
        ("TimeGenerated", "2026-10-01"),
        ("TimeGenerated", None),
        ("unknown", "x"),
        ("OperationName", "nul\x00"),
    ],
)
def test_logs_reject_wrong_types(field, value):
    with pytest.raises(ValueError):
        validate_row("StorageBlobLogs", {"TimeGenerated": "2026-10-01T00:00:00Z", field: value})


@pytest.mark.parametrize("table", list(catalog()))
def test_logs_table_columns_are_unique(table):
    columns = catalog()[table]["columns"]
    assert len({c["name"] for c in columns}) == len(columns)
    assert {"TimeGenerated", "TenantId", "Type"} <= {c["name"] for c in columns}


@pytest.mark.parametrize(
    "value", ["P1D", "PT24H", "2026-10-01T00:00:00Z/P1D", "P1D/2026-10-02T00:00:00Z"]
)
def test_iso_query_intervals(value):
    assert timespan(value, datetime(2026, 10, 2, tzinfo=UTC)) == (
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 10, 2, tzinfo=UTC),
    )
