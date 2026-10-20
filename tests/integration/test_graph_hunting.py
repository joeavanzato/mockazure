from contextlib import asynccontextmanager
from datetime import UTC, datetime

from mockazure.evidence.clock import update_settings
from mockazure.evidence.schemas import EvidenceSettings
from mockazure.hunting.repository import ingest_rows
from mockazure.hunting.schemas import HuntingProvenance
from tests.integration.graph_support import graph_client, headers_for

PATH = "/v1.0/security/runHuntingQuery"
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)


@asynccontextmanager
async def hunting_client(database, settings):
    async with graph_client(database, settings) as (client, tid, app, user):
        config = await client.put(
            f"/_mock/tenants/{tid}/hunting/settings",
            json={
                "entitlements": ["endpoint", "identity", "email", "cloud-apps", "alerts"],
            },
        )
        assert config.status_code == 200, config.text
        async with database.sessions.begin() as session:
            await update_settings(
                session, tid, EvidenceSettings(clock_mode="fixed", fixed_time=NOW)
            )
            await ingest_rows(
                session,
                tid,
                "DeviceInfo",
                [
                    {
                        "Timestamp": "2026-10-07T10:00:00Z",
                        "DeviceId": "allowed",
                        "DeviceName": "First",
                    },
                    {
                        "Timestamp": "2026-10-05T10:00:00Z",
                        "DeviceId": "hidden",
                        "DeviceName": "Second",
                    },
                ],
                ["first", "second"],
                HuntingProvenance(
                    kind="fixture",
                    source_ref="test",
                    generator_version="1",
                ),
            )
        headers = await headers_for(database, settings, tid, app, None, {"ThreatHunting.Read.All"})
        yield client, tid, app, user, headers


async def test_graph_hunting_request_schema_and_timespan_intersection(database, settings):
    async with hunting_client(database, settings) as (client, tid, app, user, headers):
        response = await client.post(
            PATH,
            headers=headers,
            json={
                "Query": "DeviceInfo | where Timestamp >= datetime(2026-10-01)"
                " | project Timestamp, DeviceId",
                "Timespan": "P1D",
            },
        )
        assert response.status_code == 200, response.text
        value = response.json()
        assert "tables" not in value and "@odata.nextLink" not in value
        assert value["schema"] == [
            {"name": "Timestamp", "type": "DateTime"},
            {"name": "DeviceId", "type": "String"},
        ]
        assert [r["DeviceId"] for r in value["results"]] == ["allowed"]
        assert value["@odata.context"].endswith("microsoft.graph.security.huntingQueryResults")
        for query in ("EmailEvents | project Fictional", "DeviceInfo | invoke external()"):
            result = await client.post(PATH, headers=headers, json={"Query": query})
            assert result.status_code == 400
        for body in (
            {"Query": "DeviceInfo", "query": "EmailEvents"},
            {"Query": "DeviceInfo", "sql": "x"},
            {"Query": "DeviceInfo", "Timespan": ""},
        ):
            assert (await client.post(PATH, headers=headers, json=body)).status_code == 400


async def test_hunting_original_graph_sdk_wire_roundtrip(database, settings):
    from msgraph.generated.security.microsoft_graph_security_run_hunting_query import (
        run_hunting_query_post_request_body,
    )

    from integrations.python.graph_directory import graph_client as sdk_client

    async with hunting_client(database, settings) as (client, tid, app, user, headers):
        graph = sdk_client(
            {"endpoints": {"graph": str(settings.endpoints.graph).rstrip("/")}},
            headers["Authorization"].split(" ", 1)[1],
            client,
        )
        response = await graph.security.microsoft_graph_security_run_hunting_query.post(
            run_hunting_query_post_request_body.RunHuntingQueryPostRequestBody(
                query="DeviceInfo | project DeviceId | take 1",
            ),
        )
        assert response.schema[0].name == "DeviceId"
        assert response.schema[0].type == "String"
        assert response.results
