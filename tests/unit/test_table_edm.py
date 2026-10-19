"""Table wire values retain EDM identity and precision independently of Graph."""

import pytest

from mockazure.providers.storage.errors import StorageError
from mockazure.storage.table_query import normalize_properties, parse_table_filter, wire_properties


def test_table_edm_and_queries():
    body = {
        "Count@odata.type": "Edm.Int64",
        "Count": "9007199254740993",
        "Binary@odata.type": "Edm.Binary",
        "Binary": "AAH/",
        "When@odata.type": "Edm.DateTime",
        "When": "2026-10-18T12:00:00Z",
        "Identifier@odata.type": "Edm.Guid",
        "Identifier": "01234567-89ab-cdef-0123-456789abcdef",
        "Name": "O'Brien",
        "Enabled": True,
        "Small": 42,
        "Rate": 1.5,
        "Absent": None,
    }
    stored = normalize_properties(body)
    output = wire_properties(stored)
    assert output["Count"] == "9007199254740993"
    assert output["Count@odata.type"] == "Edm.Int64"
    assert output["Binary"] == "AAH/"
    assert output["When@odata.type"] == "Edm.DateTime"
    assert output["Identifier"] == body["Identifier"]
    assert output["Enabled"] is True
    assert "Absent" not in output
    assert parse_table_filter("Count eq 9007199254740993L and Name eq 'O''Brien'") is not None


@pytest.mark.parametrize(
    "body",
    [
        {"N@odata.type": "Edm.Int64", "N": "9223372036854775808"},
        {"N@odata.type": "Edm.Int64", "N": 9007199254740993},
        {"N": 2147483648},
        {"N@odata.type": "Edm.Int32", "N": True},
        {"N@odata.type": "Edm.Boolean", "N": "true"},
        {"N@odata.type": "Edm.DateTime", "N": "1600-12-31T00:00:00Z"},
        {"N@odata.type": "Edm.Binary", "N": "!not-base64"},
        {"N@odata.type": "Edm.Guid", "N": "not-a-guid"},
        {"N@odata.type": "Edm.Decimal", "N": "1.0"},
        {"N": []},
        {"N": {}},
        {"bad-name": "x"},
        {"N": "x" * 32769},
    ],
)
def test_table_edm_rejects_invalid_values(body):
    with pytest.raises(StorageError):
        normalize_properties(body)


@pytest.mark.parametrize(
    "query",
    [
        "Name eq 'unterminated",
        "tolower(Name) eq 'x'",
        "Name eq 'x'; DROP TABLE storage_accounts",
        "Name eq 'x' trailing",
        "N add 2 eq 3",
        " or ".join("N eq 1" for _ in range(16)),
    ],
)
def test_table_filter_rejects_unsupported_or_excessive_queries(query):
    with pytest.raises(StorageError):
        parse_table_filter(query)
