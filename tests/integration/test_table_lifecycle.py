"""Table imports preserve typed values, rotate keys, and retire paging authority."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from mockazure.cloud.lifecycle import export_cloud, import_cloud, prune_cloud, reset_cloud
from mockazure.cloud.models import CloudEvent
from mockazure.identity.keys import read_wrapping_key
from tests.integration.graph_support import graph_client
from tests.integration.test_storage_authorization import ARGS, provision
from tests.integration.test_table_workflows import entity_url, table_call


async def test_table_restore_and_cursor_fences(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        account, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        assert (
            await table_call(client, "POST", base + "Tables", key, body={"TableName": "Durable"})
        ).status_code == 201
        for row in ("1", "2", "3"):
            assert (
                await table_call(
                    client,
                    "POST",
                    base + "Durable",
                    key,
                    body={
                        "PartitionKey": "p",
                        "RowKey": row,
                        "Count@odata.type": "Edm.Int64",
                        "Count": "9007199254740993",
                    },
                )
            ).status_code == 201
        first = await table_call(client, "GET", base + "Durable()", key, params={"$top": "1"})
        params = {
            "$top": "1",
            "NextPartitionKey": first.headers["x-ms-continuation-NextPartitionKey"],
            "NextRowKey": first.headers["x-ms-continuation-NextRowKey"],
        }
        async with database.sessions.begin() as session:
            state = await export_cloud(session, tenant)
            assert len(state["storage"]["accounts"][0]["tables"][0]["entities"]) == 3
            assert key not in json.dumps(state)
            invalid = copy.deepcopy(state)
            invalid["storage"]["accounts"][0]["tables"][0]["entities"][0]["properties"]["Count"][
                "value"
            ] = "9223372036854775808"
            session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
            with pytest.raises(Exception) as caught:
                await import_cloud(session, tenant, invalid)
            assert getattr(caught.value, "status", 422) == 422
        # A failed import must preserve the complete previous account and its key.
        assert (
            await table_call(client, "GET", entity_url(base, "Durable", "p", "1"), key)
        ).status_code == 200
        await database.engine.dispose()
        async with database.sessions.begin() as session:
            session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
            await import_cloud(session, tenant, state)
        assert (await table_call(client, "GET", base + "Durable()", key)).status_code == 403
        new = (await client.post(account + "/listKeys", params=ARGS, headers=arm)).json()["keys"][
            0
        ]["value"]
        assert new != key
        restored = await table_call(client, "GET", base + "Durable()", new)
        assert len(restored.json()["value"]) == 3
        assert all(v["Count"] == "9007199254740993" for v in restored.json()["value"])
        assert (
            await table_call(client, "GET", base + "Durable()", new, params=params)
        ).status_code == 400
        next_page = await table_call(client, "GET", base + "Durable()", new, params={"$top": "1"})
        async with database.sessions.begin() as session:
            await prune_cloud(session, tenant, datetime.now(UTC) + timedelta(hours=2), 100)
        assert (
            await table_call(
                client,
                "GET",
                base + "Durable()",
                new,
                params={
                    "$top": "1",
                    "NextPartitionKey": next_page.headers["x-ms-continuation-NextPartitionKey"],
                    "NextRowKey": next_page.headers["x-ms-continuation-NextRowKey"],
                },
            )
        ).status_code == 400
        async with database.sessions.begin() as session:
            state = await export_cloud(session, tenant)
            await reset_cloud(session, tenant, state["generation"])
        assert (await table_call(client, "GET", base + "Tables", new)).status_code == 404


async def test_table_failed_actions_emit_no_success_fact(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        assert (
            await table_call(client, "POST", base + "Tables", key, body={"TableName": "Evidence"})
        ).status_code == 201
        async with database.sessions() as session:
            before = await session.scalar(
                select(func.count()).select_from(CloudEvent).where(CloudEvent.tenant_id == tenant)
            )
        result = await table_call(
            client,
            "POST",
            base + "Evidence",
            key,
            body={"PartitionKey": "p", "RowKey": "r", "Count": []},
        )
        assert result.status_code == 400
        async with database.sessions() as session:
            after = await session.scalar(
                select(func.count()).select_from(CloudEvent).where(CloudEvent.tenant_id == tenant)
            )
        assert before == after


async def test_table_migration_refuses_lossy_downgrade(database, settings):
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration

    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        assert (
            await table_call(
                client, "POST", endpoints["table"] + "Tables", key, body={"TableName": "Preserved"}
            )
        ).status_code == 201
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with pytest.raises(DBAPIError, match="Export and reset Table state"):
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "063")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "067"
            assert connection.scalar(text("SELECT name FROM storage_tables")) == "Preserved"
    finally:
        engine.dispose()
