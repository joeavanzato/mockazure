import hashlib

import pytest

NAMES = {
    "IdentityInfo",
    "IdentityLogonEvents",
    "IdentityDirectoryEvents",
    "IdentityQueryEvents",
    "EmailEvents",
    "EmailAttachmentInfo",
    "EmailUrlInfo",
    "EmailPostDeliveryEvents",
    "UrlClickEvents",
    "DeviceInfo",
    "DeviceNetworkInfo",
    "DeviceLogonEvents",
    "DeviceProcessEvents",
    "DeviceNetworkEvents",
    "DeviceFileEvents",
    "DeviceRegistryEvents",
    "DeviceEvents",
    "CloudAppEvents",
    "AlertInfo",
    "AlertEvidence",
}


def test_exact_twenty_table_catalog():
    from mockazure.hunting.contracts import catalog, table_contract, verify_sources

    assert set(catalog()) == NAMES
    assert verify_sources()["tables"] == 20
    for name in NAMES:
        value = table_contract(name)
        assert value.name == name and value.source_refs
        assert value.columns["Timestamp"] == "datetime"
        assert value.availability in {"available", "preview", "conditional"}
    with pytest.raises(ValueError):
        table_contract("AADSignInEventsBeta")


def test_columns_and_types_match_pinned_sources():
    from mockazure.hunting.contracts import DATA, catalog, sources
    from tools.acquire_hunting_contracts import parse_columns

    for name, contract in catalog().items():
        source = sources()[contract.source_refs[0]]
        content = (DATA / source["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == source["sha256"]
        columns, _ = parse_columns(content)
        assert contract.columns == columns


def test_ingestion_rejects_unknown_columns_and_wrong_types():
    from mockazure.hunting.schemas import validate_row

    for body in (
        {"Timestamp": "2026-10-07T00:00:00Z", "Invented": "x"},
        {"Timestamp": "not-a-date"},
        {"Timestamp": "2026-10-07T00:00:00Z", "FileSize": True},
        {"Timestamp": "2026-10-07T00:00:00Z", "DeviceId": 42},
    ):
        with pytest.raises(ValueError):
            validate_row("DeviceFileEvents", body)
    assert validate_row("DeviceFileEvents", {"Timestamp": "2026-10-07T00:00:00Z"}) == {
        "Timestamp": "2026-10-07T00:00:00Z",
    }
