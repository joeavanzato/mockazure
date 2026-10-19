"""Published Storage source closure must stay hash-bound and offline."""

import hashlib
import json
from pathlib import Path


def test_storage_sources_and_operation_schema_references():
    from mockazure.cloud.contracts import DATA, _catalog, verify_references

    verify_references("storage")
    assert _catalog("storage") == json.loads(
        Path("integrations/contracts/cloud/storage.json").read_text()
    )
    for row in _catalog("storage")["operations"]:
        document = json.loads((DATA / "storage" / row["sources"][0]["localPath"]).read_text())
        operations = [
            op
            for path in (document.get("paths", {}) | document.get("x-ms-paths", {})).values()
            for op in path.values()
            if isinstance(op, dict) and op.get("operationId") == row["nativeOperationId"]
        ]
        assert len(operations) == 1
        assert all(
            int(status) in row["statusCodes"]
            for status in operations[0]["responses"]
            if status.isdigit()
        )
    root = DATA / "storage"
    for source in json.loads((root / "protocol-references.json").read_text())["sources"]:
        assert (
            hashlib.sha256((root / source["localPath"]).read_bytes()).hexdigest()
            == source["sha256"]
        )
        assert source["url"].startswith("https://learn.microsoft.com/")
