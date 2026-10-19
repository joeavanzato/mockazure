"""Native batch representation selectors retain strict request boundaries."""

import pytest
from starlette.requests import Request

from mockazure.providers.storage.errors import StorageError
from mockazure.providers.storage.table_routes import parse_batch
from tests.integration.test_table_workflows import batch


def parse(query):
    request = Request(
        {
            "type": "http",
            "scheme": "https",
            "method": "POST",
            "path": "/account/$batch",
            "query_string": b"",
            "headers": [
                (b"host", b"localhost:8447"),
                (b"content-type", b"multipart/mixed; boundary=batch_test"),
            ],
        }
    )
    raw = batch(
        "https://localhost:8447/account/",
        [("POST", "Evidence" + query, {"PartitionKey": "p", "RowKey": "r"}, None)],
    )
    return parse_batch(request, raw, "account")


@pytest.mark.parametrize("mode", ["minimalmetadata", "nometadata", "fullmetadata"])
def test_batch_accepts_native_format(mode):
    name, operations = parse("?$format=application%2Fjson%3Bodata%3D" + mode)
    assert name == "Evidence" and operations[0]["mode"] == mode


@pytest.mark.parametrize(
    "query",
    [
        "?$filter=x",
        "?$format=json&$format=json",
        "?$format=application/xml",
        "?$format=application/json;odata=bad",
        "?sig=x",
        "?$format",
    ],
)
def test_batch_rejects_invalid_embedded_query(query):
    with pytest.raises(StorageError):
        parse(query)
