"""Independent Table wire requests against the actual local service listener."""

import asyncio
import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import quote, urlsplit

from tests.integration.graph_support import graph_client
from tests.integration.test_storage_authorization import provision


async def table_call(client, method, url, key, *, body=None, headers=None, params=None, raw=None):
    payload = json.dumps(body).encode() if body is not None else (raw or b"")
    head = {
        "x-ms-version": "2019-02-02",
        "x-ms-date": format_datetime(datetime.now(UTC), usegmt=True),
        "Accept": "application/json;odata=minimalmetadata",
    } | (headers or {})
    if body is not None:
        head["Content-Type"] = "application/json"
    account = urlsplit(url).path.split("/")[1]
    canonical = "\n".join(
        [
            method,
            "",
            head.get("Content-Type", ""),
            head["x-ms-date"],
            "/" + account + urlsplit(url).path,
        ]
    )
    sig = base64.b64encode(
        hmac.new(base64.b64decode(key), canonical.encode(), hashlib.sha256).digest()
    ).decode()
    head["Authorization"] = f"SharedKey {account}:{sig}"
    return await client.request(method, url, content=payload, headers=head, params=params)


def entity_url(base, table, partition, row):
    return (
        base
        + table
        + "(PartitionKey='"
        + quote(partition.replace("'", "''"), safe="'")
        + "',RowKey='"
        + quote(row.replace("'", "''"), safe="'")
        + "')"
    )


def batch(base, operations):
    lines = ["--batch_test", "Content-Type: multipart/mixed; boundary=changeset_test", ""]
    for i, (method, path, body, etag) in enumerate(operations):
        lines += [
            "--changeset_test",
            "Content-Type: application/http",
            "Content-Transfer-Encoding: binary",
            f"Content-ID: {i + 1}",
            "",
            f"{method} {base}{path} HTTP/1.1",
            "Content-Type: application/json",
            "Accept: application/json;odata=minimalmetadata",
        ]
        if etag:
            lines += ["If-Match: " + etag]
        lines += ["", json.dumps(body) if body is not None else ""]
    lines += ["--changeset_test--", "--batch_test--", ""]
    return "\r\n".join(lines).encode()


async def test_table_edm_and_queries(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        result = await table_call(
            client, "POST", base + "Tables", key, body={"TableName": "Evidence"}
        )
        assert result.status_code == 201, result.text
        assert (
            await table_call(client, "POST", base + "Tables", key, body={"TableName": "evidence"})
        ).status_code == 409
        for i in range(3):
            body = {
                "PartitionKey": "O'Brien",
                "RowKey": str(i),
                "Count@odata.type": "Edm.Int64",
                "Count": str(9007199254740993 + i),
                "Bytes@odata.type": "Edm.Binary",
                "Bytes": "AAH/",
                "When@odata.type": "Edm.DateTime",
                "When": "2026-10-18T00:00:00Z",
                "Optional": None,
            }
            inserted = await table_call(client, "POST", base + "Evidence", key, body=body)
            assert inserted.status_code == 201, inserted.text
        params = {
            "$filter": "PartitionKey eq 'O''Brien' and Count ge 9007199254740993L",
            "$select": "PartitionKey,RowKey,Count,Bytes,When",
            "$top": "1",
        }
        values = []
        while True:
            page = await table_call(client, "GET", base + "evidence()", key, params=params)
            assert page.status_code == 200, page.text
            values += page.json()["value"]
            token = page.headers.get("x-ms-continuation-NextPartitionKey")
            if not token:
                break
            params["NextPartitionKey"] = token
            params["NextRowKey"] = page.headers["x-ms-continuation-NextRowKey"]
        assert [v["Count"] for v in values] == [
            "9007199254740993",
            "9007199254740994",
            "9007199254740995",
        ]
        assert all(v["Count@odata.type"] == "Edm.Int64" and v["Bytes"] == "AAH/" for v in values)
        assert len({v["RowKey"] for v in values}) == 3
        read = await table_call(client, "GET", entity_url(base, "Evidence", "O'Brien", "0"), key)
        assert read.status_code == 200 and read.json()["Count"] == "9007199254740993"
        assert "Optional" not in read.json()
        assert (
            read.headers["ETag"]
            == "W/\"datetime'" + quote(read.json()["Timestamp"], safe="") + "'\""
        )
        no_meta = await table_call(
            client,
            "GET",
            entity_url(base, "Evidence", "O'Brien", "0"),
            key,
            headers={"Accept": "application/json;odata=nometadata"},
        )
        assert not any("@odata" in k or k.startswith("odata.") for k in no_meta.json())


async def test_table_batch_rollback(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        assert (
            await table_call(client, "POST", base + "Tables", key, body={"TableName": "Evidence"})
        ).status_code == 201
        one = {"PartitionKey": "p", "RowKey": "one", "Value": 1}
        assert (
            await table_call(client, "POST", base + "Evidence", key, body=one)
        ).status_code == 201
        operations = [
            ("POST", "Evidence", {"PartitionKey": "p", "RowKey": "two", "Value": 2}, None),
            ("POST", "Evidence", one, None),
        ]
        result = await table_call(
            client,
            "POST",
            base + "$batch",
            key,
            raw=batch(base, operations),
            headers={"Content-Type": "multipart/mixed; boundary=batch_test"},
        )
        assert result.status_code == 202 and "409 Conflict" in result.text, result.text
        assert (
            await table_call(client, "GET", entity_url(base, "Evidence", "p", "two"), key)
        ).status_code == 404
        operations[1] = ("POST", "Evidence", {"PartitionKey": "other", "RowKey": "three"}, None)
        invalid = await table_call(
            client,
            "POST",
            base + "$batch",
            key,
            raw=batch(base, operations),
            headers={"Content-Type": "multipart/mixed; boundary=batch_test"},
        )
        assert invalid.status_code == 202 and "400 Bad Request" in invalid.text, invalid.text
        assert (
            await table_call(client, "GET", entity_url(base, "Evidence", "p", "two"), key)
        ).status_code == 404
        operations[1] = ("POST", "Evidence", {"PartitionKey": "p", "RowKey": "three"}, None)
        good = await table_call(
            client,
            "POST",
            base + "$batch",
            key,
            raw=batch(base, operations),
            headers={"Content-Type": "multipart/mixed; boundary=batch_test"},
        )
        assert good.status_code == 202 and good.text.count("201 Created") == 2, good.text


async def test_table_etag_race(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        assert (
            await table_call(client, "POST", base + "Tables", key, body={"TableName": "Evidence"})
        ).status_code == 201
        created = await table_call(
            client,
            "POST",
            base + "Evidence",
            key,
            body={"PartitionKey": "p", "RowKey": "r", "Keep": True},
        )
        url = entity_url(base, "Evidence", "p", "r")
        responses = await asyncio.gather(
            *[
                table_call(
                    client,
                    "PATCH",
                    url,
                    key,
                    body={"Value": n},
                    headers={"If-Match": created.headers["ETag"]},
                )
                for n in (1, 2)
            ]
        )
        assert sorted(r.status_code for r in responses) == [204, 412]
        value = (await table_call(client, "GET", url, key)).json()
        assert value["Keep"] is True
        assert (await table_call(client, "PUT", url, key, body={"Value": 3})).status_code == 204
        assert "Keep" not in (await table_call(client, "GET", url, key)).json()


async def test_table_binary_filter_uses_bytes_and_delete_is_durable(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        await table_call(client, "POST", base + "Tables", key, body={"TableName": "BinaryValues"})
        for row, value in [("zero", "AA=="), ("high", "/w==")]:
            assert (
                await table_call(
                    client,
                    "POST",
                    base + "BinaryValues",
                    key,
                    body={
                        "PartitionKey": "p",
                        "RowKey": row,
                        "Bytes@odata.type": "Edm.Binary",
                        "Bytes": value,
                    },
                )
            ).status_code == 201
        result = await table_call(
            client, "GET", base + "BinaryValues()", key, params={"$filter": "Bytes gt X'00'"}
        )
        assert result.status_code == 200, result.text
        assert [row["RowKey"] for row in result.json()["value"]] == ["high"]
        assert (
            await table_call(client, "DELETE", base + "Tables('BinaryValues')", key)
        ).status_code == 204
        assert (await table_call(client, "GET", base + "BinaryValues()", key)).status_code == 404
        assert (
            await table_call(
                client, "POST", base + "Tables", key, body={"TableName": "BinaryValues"}
            )
        ).status_code == 201
        assert (await table_call(client, "GET", base + "BinaryValues()", key)).json()["value"] == []
