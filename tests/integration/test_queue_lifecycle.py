"""Queue restore clears write authority and cleanup removes expired messages."""

import copy
import json
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import func, select

from mockazure.cloud.lifecycle import export_cloud, import_cloud, prune_cloud, reset_cloud
from mockazure.cloud.models import CloudEvent
from mockazure.identity.keys import read_wrapping_key
from tests.integration.graph_support import graph_client
from tests.integration.test_queue_workflows import message, messages, queue_call
from tests.integration.test_storage_authorization import ARGS, provision


async def test_queue_restore_receipts_cursors_and_cleanup(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        account, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["queue"]
        for name in ("queue-one", "queue-two"):
            assert (await queue_call(client, "PUT", base + name, key)).status_code == 201
        url = base + "queue-one/messages"
        sent = messages(
            await queue_call(
                client, "POST", url, key, {"messagettl": "60"}, message("private-payload")
            )
        )[0]
        first = await queue_call(client, "GET", base, key, {"comp": "list", "maxresults": "1"})
        marker = ET.fromstring(first.content).findtext("NextMarker")
        async with database.sessions.begin() as session:
            state = await export_cloud(session, tenant)
            assert len(state["storage"]["accounts"][0]["queue_messages"]) == 1
            assert sent["PopReceipt"] not in json.dumps(state)
            bad = copy.deepcopy(state)
            bad["storage"]["accounts"][0]["queue_messages"][0]["body"] = "x" * 65537
            session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
            with pytest.raises(Exception) as caught:
                await import_cloud(session, tenant, bad)
            assert getattr(caught.value, "status", 422) == 422
            facts = list(
                await session.scalars(select(CloudEvent).where(CloudEvent.tenant_id == tenant))
            )
            assert all(
                "private-payload" not in json.dumps(f.payload)
                and sent["PopReceipt"] not in json.dumps(f.payload)
                for f in facts
            )
        assert (
            messages(await queue_call(client, "GET", url, key, {"peekonly": "true"}))[0][
                "MessageText"
            ]
            == "private-payload"
        )
        async with database.sessions.begin() as session:
            session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
            await import_cloud(session, tenant, state)
        assert (await queue_call(client, "GET", url, key)).status_code == 403
        new = (await client.post(account + "/listKeys", params=ARGS, headers=arm)).json()["keys"][
            0
        ]["value"]
        assert (
            await queue_call(
                client, "GET", base, new, {"comp": "list", "maxresults": "1", "marker": marker}
            )
        ).status_code == 400
        assert (
            await queue_call(
                client,
                "DELETE",
                url + "/" + sent["MessageId"],
                new,
                {"popreceipt": sent["PopReceipt"]},
            )
        ).status_code in {400, 404}
        received = messages(await queue_call(client, "GET", url, new))[0]
        assert received["MessageText"] == "private-payload"
        from mockazure.storage.queue_models import QueueMessage

        async with database.sessions.begin() as session:
            await prune_cloud(session, tenant, datetime.now(UTC) + timedelta(hours=2), 100)
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(QueueMessage)
                    .where(QueueMessage.tenant_id == tenant)
                )
                == 0
            )
            state = await export_cloud(session, tenant)
            await reset_cloud(session, tenant, state["generation"])
        assert (await queue_call(client, "GET", url, new)).status_code == 404


async def test_queue_current_receipt_survives_visibility_and_invalid_update(
    database, settings, monkeypatch
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["queue"] + "receipts"
        assert (await queue_call(client, "PUT", url, key)).status_code == 201
        from mockazure.providers.storage import queue_routes

        now = datetime.now(UTC)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        monkeypatch.setattr(queue_routes, "datetime", Clock)
        sent = messages(
            await queue_call(
                client, "POST", url + "/messages", key, {"messagettl": "60"}, message("original")
            )
        )[0]
        target = url + "/messages/" + sent["MessageId"]
        rejected = await queue_call(
            client,
            "PUT",
            target,
            key,
            {"popreceipt": sent["PopReceipt"], "visibilitytimeout": "61"},
            message("bad"),
        )
        assert rejected.status_code == 400
        now += timedelta(seconds=31)
        assert (
            await queue_call(client, "DELETE", target, key, {"popreceipt": sent["PopReceipt"]})
        ).status_code == 204


async def test_queue_migration_refuses_lossy_downgrade(database, settings):
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration

    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        assert (
            await queue_call(client, "PUT", endpoints["queue"] + "preserved", key)
        ).status_code == 201
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with pytest.raises(DBAPIError, match="Export and reset Queue state"):
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "064")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == ScriptDirectory.from_config(configuration()).get_current_head()
            )
            assert (
                connection.scalar(text("SELECT name FROM storage_namespaces WHERE service='queue'"))
                == "preserved"
            )
    finally:
        engine.dispose()
