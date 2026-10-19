"""Published Storage signing strings preserve wire bytes and repeated fields."""

from importlib import import_module
from pathlib import Path

import pytest


def canonicalize(*args):
    assert Path("src/mockazure/storage/signing.py").exists(), "Storage signing is absent"
    return import_module("mockazure.storage.signing").canonicalize_storage_request(*args)


def test_blob_shared_key_matches_published_zero_length_vector():
    headers = [
        ("x-ms-date", "Fri, 26 Jun 2015 23:39:12 GMT"),
        ("x-ms-version", "2025-11-05"),
        ("Content-Length", "0"),
    ]
    expected = (
        "GET"
        + "\n" * 12
        + "x-ms-date:Fri, 26 Jun 2015 23:39:12 GMT\nx-ms-version:2025-11-05\n"
        + "/myaccount/mycontainer\ncomp:metadata\nrestype:container\ntimeout:20"
    )
    assert (
        canonicalize(
            "GET",
            b"/mycontainer",
            [("restype", "container"), ("timeout", "20"), ("comp", "metadata")],
            headers,
            "myaccount",
            "blob",
            "2025-11-05",
        )
        == expected.encode()
    )


def test_path_addressing_encoded_names_and_repeated_headers_are_preserved():
    headers = [
        ("x-ms-version", "2025-11-05"),
        ("X-MS-Meta-Note", "  first  "),
        ("x-ms-meta-note", "second"),
        ("x-ms-empty", ""),
    ]
    result = canonicalize(
        "GET",
        b"/localaccount/files/a%2Fb%20c",
        [("include", "metadata"), ("Include", "snapshots"), ("comp", "list")],
        headers,
        "localaccount",
        "file",
        "2025-11-05",
    )
    assert result.endswith(
        b"/localaccount/localaccount/files/a%2Fb%20c\ncomp:list\ninclude:metadata,snapshots"
    )
    assert b"x-ms-meta-note:first,second\n" in result and b"x-ms-empty:\n" in result
    with pytest.raises(ValueError):
        canonicalize(
            "PUT",
            b"/localaccount/files",
            [],
            [("Content-Length", "0"), ("content-length", "1")],
            "localaccount",
            "file",
            "2025-11-05",
        )


def test_table_shared_key_uses_date_without_canonicalized_xms_headers():
    result = canonicalize(
        "GET",
        b"/localaccount/Tables",
        [("$filter", "TableName eq 'evidence'")],
        [("x-ms-date", "Fri, 26 Jun 2015 23:39:12 GMT"), ("x-ms-version", "2025-11-05")],
        "localaccount",
        "table",
        "2025-11-05",
    )
    assert result == b"GET\n\n\nFri, 26 Jun 2015 23:39:12 GMT\n/localaccount/localaccount/Tables"


def test_absent_xms_headers_are_not_synthesized_into_date_signature():
    value = canonicalize(
        "GET",
        b"/account/box",
        [],
        [("Date", "Fri, 26 Jun 2015 23:39:12 GMT")],
        "account",
        "blob",
        "2025-11-05",
    )
    assert b"x-ms-date:" not in value and b"x-ms-version:" not in value
    assert b"Fri, 26 Jun 2015 23:39:12 GMT" in value
