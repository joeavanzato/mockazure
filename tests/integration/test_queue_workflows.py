"""Queue wire delivery, redelivery and independent current pop receipts."""

import asyncio
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

from tests.integration.graph_support import graph_client
from tests.integration.test_blob_workflows import call
from tests.integration.test_storage_authorization import provision

VERSION = "2026-02-06"


async def queue_call(client, method, url, key, query=None, body=b"", extra=None):
    return await call(
        client,
        method,
        url,
        key,
        query,
        body,
        {"x-ms-version": VERSION, "Content-Type": "application/xml"} | (extra or {}),
    )


def message(text):
    root = ET.Element("QueueMessage")
    ET.SubElement(root, "MessageText").text = text
    return ET.tostring(root, encoding="utf-8")


def messages(response):
    assert response.status_code in {200, 201}, response.text
    return [
        {node.tag: node.text for node in item}
        for item in ET.fromstring(response.content).findall("QueueMessage")
    ]


async def test_queue_redelivery_and_receipts(database, settings, monkeypatch):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["queue"] + "work-items"
        created = await queue_call(client, "PUT", url, key, extra={"x-ms-meta-owner": "test"})
        assert created.status_code == 201, created.text
        assert (
            await queue_call(client, "PUT", url, key, extra={"x-ms-meta-owner": "test"})
        ).status_code == 204
        assert (
            await queue_call(client, "PUT", url, key, extra={"x-ms-meta-owner": "other"})
        ).status_code == 409
        from mockazure.providers.storage import queue_routes

        now = datetime.now(UTC)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(queue_routes, "datetime", Clock)
        enqueued = messages(
            await queue_call(
                client, "POST", url + "/messages", key, body=message("raw <payload> & unicode ☃")
            )
        )[0]
        assert enqueued["MessageId"] and enqueued["PopReceipt"]
        peek = messages(
            await queue_call(client, "GET", url + "/messages", key, {"peekonly": "true"})
        )[0]
        assert peek["MessageText"] == "raw <payload> & unicode ☃" and peek["DequeueCount"] == "0"
        assert "PopReceipt" not in peek and "TimeNextVisible" not in peek
        first = messages(
            await queue_call(client, "GET", url + "/messages", key, {"visibilitytimeout": "30"})
        )[0]
        assert first["DequeueCount"] == "1"
        assert messages(await queue_call(client, "GET", url + "/messages", key)) == []
        await database.engine.dispose()
        now += timedelta(seconds=31)
        second = messages(await queue_call(client, "GET", url + "/messages", key))[0]
        assert second["DequeueCount"] == "2" and second["PopReceipt"] != first["PopReceipt"]
        mid = url + "/messages/" + second["MessageId"]
        stale = await queue_call(client, "DELETE", mid, key, {"popreceipt": first["PopReceipt"]})
        assert (
            stale.status_code == 400
            and ET.fromstring(stale.content).findtext("Code") == "PopReceiptMismatch"
        )
        changed = await queue_call(
            client,
            "PUT",
            mid,
            key,
            {"popreceipt": second["PopReceipt"], "visibilitytimeout": "0"},
            message("changed"),
        )
        assert (
            changed.status_code == 204
            and changed.headers["x-ms-popreceipt"] != second["PopReceipt"]
        ), changed.text
        third = messages(await queue_call(client, "GET", url + "/messages", key))[0]
        assert third["MessageText"] == "changed" and third["DequeueCount"] == "3"
        assert (
            await queue_call(client, "DELETE", mid, key, {"popreceipt": third["PopReceipt"]})
        ).status_code == 204
        assert messages(await queue_call(client, "GET", url + "/messages", key)) == []
        props = await queue_call(client, "GET", url, key, {"comp": "metadata"})
        assert (
            props.headers["x-ms-approximate-messages-count"] == "0"
            and props.headers["x-ms-meta-owner"] == "test"
        )


async def test_queue_workers_and_ttl(database, settings, monkeypatch):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["queue"] + "parallel-work"
        result = await queue_call(client, "PUT", url, key)
        assert result.status_code == 201, result.text
        from mockazure.providers.storage import queue_routes

        now = datetime.now(UTC)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(queue_routes, "datetime", Clock)
        for n in range(8):
            assert (
                await queue_call(
                    client, "POST", url + "/messages", key, {"messagettl": "10"}, message(str(n))
                )
            ).status_code == 201
        left, right = await asyncio.gather(
            *[
                queue_call(
                    client,
                    "GET",
                    url + "/messages",
                    key,
                    {"numofmessages": "4", "visibilitytimeout": "30"},
                )
                for _ in range(2)
            ]
        )
        ids = [{m["MessageId"] for m in messages(r)} for r in (left, right)]
        assert len(ids[0]) == len(ids[1]) == 4 and ids[0].isdisjoint(ids[1])
        now += timedelta(seconds=11)
        assert messages(await queue_call(client, "GET", url + "/messages", key)) == []
        props = await queue_call(client, "GET", url, key, {"comp": "metadata"})
        assert props.headers["x-ms-approximate-messages-count"] == "0"
        sent = messages(
            await queue_call(
                client, "POST", url + "/messages", key, {"messagettl": "-1"}, message("forever")
            )
        )[0]
        assert sent["ExpirationTime"].endswith("9999 23:59:59 GMT")
        assert (await queue_call(client, "DELETE", url + "/messages", key)).status_code == 204
        assert (
            messages(await queue_call(client, "GET", url + "/messages", key, {"peekonly": "true"}))
            == []
        )


async def test_queue_protocol_limits_and_namespace_list(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["queue"]
        for name in ("queue-one", "queue-two"):
            result = await queue_call(client, "PUT", base + name, key)
            assert result.status_code == 201, result.text
        first = await queue_call(
            client, "GET", base, key, {"comp": "list", "maxresults": "1", "include": "metadata"}
        )
        root = ET.fromstring(first.content)
        marker = root.findtext("NextMarker")
        assert root.findtext(".//Queue/Name") == "queue-one" and marker
        second = await queue_call(
            client,
            "GET",
            base,
            key,
            {"comp": "list", "maxresults": "1", "include": "metadata", "marker": marker},
        )
        assert ET.fromstring(second.content).findtext(".//Queue/Name") == "queue-two"
        url = base + "queue-one"
        assert (
            await queue_call(
                client,
                "PUT",
                url,
                key,
                {"comp": "metadata"},
                extra={"x-ms-meta-purpose": "evidence"},
            )
        ).status_code == 204
        assert (await queue_call(client, "GET", url, key, {"comp": "metadata"})).headers[
            "x-ms-meta-purpose"
        ] == "evidence"
        for params in (
            {"numofmessages": "33"},
            {"numofmessages": "0"},
            {"visibilitytimeout": "0"},
            {"visibilitytimeout": "604801"},
            {"unknown": "x"},
        ):
            assert (
                await queue_call(client, "GET", url + "/messages", key, params)
            ).status_code == 400
        for params in (
            {"messagettl": "0"},
            {"messagettl": "-2"},
            {"visibilitytimeout": "10", "messagettl": "10"},
        ):
            assert (
                await queue_call(client, "POST", url + "/messages", key, params, message("x"))
            ).status_code == 400
        too_large = await queue_call(
            client, "POST", url + "/messages", key, body=message("x" * 65537)
        )
        assert (
            too_large.status_code == 400
            and ET.fromstring(too_large.content).findtext("Code") == "MessageTooLarge"
        )
        assert (await queue_call(client, "DELETE", url, key)).status_code == 204
        assert (await queue_call(client, "GET", url + "/messages", key)).status_code == 404
