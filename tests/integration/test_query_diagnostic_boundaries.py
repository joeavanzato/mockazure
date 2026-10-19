"""Execution limits and isolation apply to real API inputs and durable jobs."""

from datetime import UTC, datetime

import pytest

from mockazure.query.compiler import evaluate_query
from mockazure.query.parser import parse_query
from mockazure.query.types import QueryError, QueryLimits, QueryResult
from tests.integration.graph_support import graph_client
from tests.integration.test_log_analytics_queries import ingest, workspace


def test_query_aggregate_memory_budget():
    # Each individual table fits; retained let bindings together exceed the cap.
    table = QueryResult([("n", "long")], [[i] for i in range(10)])
    plan = parse_query(
        "let a = Resources | project n; let b = Resources | project n; "
        "let c = Resources | project n; c",
        "loganalytics",
    )
    with pytest.raises(QueryError, match="memory") as error:
        evaluate_query(
            plan, {"Resources": table}, datetime.now(UTC), limits=QueryLimits(bytes=2500)
        )
    assert error.value.code == "QueryLimitExceeded"


@pytest.mark.parametrize(
    "limits",
    [
        {"rows": 1},
        {"bytes": 200},
        {"expanded": 1},
        {"seconds": 0.000001},
    ],
)
async def test_query_budget_and_isolation(database, settings, limits):
    async with graph_client(database, settings) as (client, tenant, app, _):
        wid, _, _, logs, _ = await workspace(client, database, settings, tenant, app)
        await ingest(database, tenant, wid)
        info = dict(database.sessions.kw.get("info", {}))
        database.sessions.configure(info=info | {"query_limits": limits})
        response = await client.post(
            settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
            headers=logs,
            json={"query": "StorageBlobLogs | project AccountName"},
        )
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "QueryExecutionError"
        # A rejected query leaves the connection/transaction usable.
        database.sessions.configure(info=info)
        response = await client.post(
            settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
            headers=logs,
            json={"query": "StorageBlobLogs | summarize n=count()"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["tables"][0]["rows"] == [[3]]


async def test_cross_tenant_join(database, settings):
    from uuid import UUID

    from tests.integration.graph_support import registered_app

    async with graph_client(database, settings) as (client, tenant, app, _):
        wid, _, _, logs, _ = await workspace(client, database, settings, tenant, app)
        await ingest(database, tenant, wid)
        response = await client.post(
            "/_mock/tenants",
            json={
                "slug": "foreign",
                "display_name": "Foreign",
                "domain": "foreign.test",
                "licences": ["E5"],
            },
        )
        assert response.status_code == 201, response.text
        foreign = UUID(response.json()["id"])
        foreign_app = UUID(await registered_app(client, foreign))
        other, _, _, _, _ = await workspace(client, database, settings, foreign, foreign_app)
        await ingest(database, foreign, other)
        query = (
            "StorageBlobLogs | join kind=inner (StorageBlobLogs) on AccountName "
            "| summarize n=count()"
        )
        url = settings.endpoints.logs + f"/v1/workspaces/{wid}/query"
        response = await client.post(url, headers=logs, json={"query": query})
        assert response.status_code == 200, response.text
        assert response.json()["tables"][0]["rows"] == [[9]]
        response = await client.post(
            url, headers=logs, json={"query": query, "workspaces": [str(other)]}
        )
        assert response.status_code == 403 and "tables" not in response.json(), response.text


async def test_diagnostic_restart_export(database, settings):
    from datetime import timedelta
    from uuid import uuid4

    from mockazure.db.engine import close_database, create_database
    from mockazure.jobs.dispatch import dispatch_job
    from mockazure.jobs.repository import claim_job
    from tests.integration.test_blob_workflows import call
    from tests.integration.test_diagnostic_delivery import VERSION, drain, setup

    async with graph_client(database, settings) as (client, tenant, app, _):
        _, _, path, body, arm, endpoints, key, wid, logs = await setup(
            client, database, settings, tenant, app
        )
        assert (await client.put(path, headers=arm, params=VERSION, json=body)).status_code == 200
        assert (
            await call(client, "PUT", endpoints["blob"] + "recovery", key, {"restype": "container"})
        ).status_code == 201
        now = datetime.now(UTC)
        async with database.sessions.begin() as session:
            old = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=60,
                tenant_id=tenant,
                kind="monitor.diagnostic",
            )
        assert old is not None
        admin = f"/_mock/tenants/{tenant}/cloud"
        snapshot = (await client.get(admin + "/state")).json()
        ids = {d["event"]["id"] for d in snapshot["diagnostics"]["deliveries"]}
        assert (await client.put(admin + "/state", json=snapshot)).status_code == 200
        restarted = create_database(settings)
        try:
            assert (await dispatch_job(restarted, old, now)).state == "stale"
            done = await drain(restarted, tenant, now + timedelta(seconds=1))
            assert len(done) == 2 and all(r.state == "succeeded" for r in done)
            assert {r.result["sourceId"] for r in done} == ids
            assert await drain(restarted, tenant, now + timedelta(seconds=2)) == []
        finally:
            await close_database(restarted)
        response = await client.post(
            settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
            headers=logs,
            json={"query": "StorageBlobLogs | summarize n=count()"},
        )
        assert response.status_code == 200 and response.json()["tables"][0]["rows"] == [[1]], (
            response.text
        )


async def test_configured_diagnostic_delay_retry(database, settings, monkeypatch):
    from datetime import timedelta

    from mockazure.monitor import publication
    from tests.integration.test_blob_workflows import call
    from tests.integration.test_diagnostic_delivery import VERSION, drain, setup

    info = dict(database.sessions.kw.get("info", {}))
    database.sessions.configure(
        info=info | {"diagnostic_delivery": {"delay": 20, "retry": 30, "attempts": 2}}
    )
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, _, path, body, arm, endpoints, key, _, _ = await setup(
            client, database, settings, tenant, app
        )
        assert (await client.put(path, headers=arm, params=VERSION, json=body)).status_code == 200
        assert (
            await call(client, "PUT", endpoints["blob"] + "delayed", key, {"restype": "container"})
        ).status_code == 201
        now = datetime.now(UTC)
        assert await drain(database, tenant, now) == []

        async def fault(*args, **kwargs):
            raise OSError("injected local sink failure")

        monkeypatch.setattr(publication, "deliver_blob", fault)
        first = await drain(database, tenant, now + timedelta(seconds=21))
        assert sorted(r.state for r in first) == ["pending", "succeeded"]
        assert await drain(database, tenant, now + timedelta(seconds=45)) == []
        last = await drain(database, tenant, now + timedelta(seconds=52))
        assert len(last) == 1 and last[0].state == "failed"


async def test_database_timeout_does_not_poison_batch(database, settings, monkeypatch):
    from sqlalchemy import text

    from mockazure.monitor import logs as module

    original = module.input_tables
    calls = 0

    async def slow_once(session, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await session.execute(text("select pg_sleep(0.3)"))
        return await original(session, *args, **kwargs)

    async with graph_client(database, settings) as (client, tenant, app, _):
        wid, _, _, logs, _ = await workspace(client, database, settings, tenant, app)
        await ingest(database, tenant, wid)
        info = dict(database.sessions.kw.get("info", {}))
        database.sessions.configure(info=info | {"query_limits": {"seconds": 0.1}})
        monkeypatch.setattr(module, "input_tables", slow_once)
        response = await client.post(
            settings.endpoints.logs + "/v1/$batch",
            headers=logs,
            json={
                "requests": [
                    {
                        "id": label,
                        "method": "POST",
                        "path": "/query",
                        "workspace": str(wid),
                        "body": {"query": "StorageBlobLogs | summarize n=count()"},
                    }
                    for label in ("slow", "next")
                ]
            },
        )
        assert response.status_code == 200, response.text
        rows = response.json()["responses"]
        assert [r["status"] for r in rows] == [400, 200], rows
        assert rows[0]["body"]["error"]["code"] == "QueryExecutionError"
        assert rows[1]["body"]["tables"][0]["rows"] == [[3]]


async def test_cpu_cancellation_releases_api_and_stops_worker(database, settings, monkeypatch):
    import asyncio
    from threading import Event

    from mockazure.query import compiler

    started, stopped = Event(), Event()

    def cpu(plan, tables, now, *, limits, cancelled):
        budget = compiler.Budget(limits, cancelled)
        started.set()
        try:
            while True:
                budget.tick()
        finally:
            stopped.set()

    async with graph_client(database, settings) as (client, tenant, app, _):
        wid, _, _, logs, _ = await workspace(client, database, settings, tenant, app)
        await ingest(database, tenant, wid)
        monkeypatch.setattr(compiler, "evaluate_query", cpu)
        task = asyncio.create_task(
            client.post(
                settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
                headers=logs,
                json={"query": "StorageBlobLogs"},
            )
        )
        # to_thread keeps the event loop free while real evaluation is active.
        assert await asyncio.to_thread(started.wait, 3)
        assert not stopped.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(stopped.wait, 2)


async def test_native_sdk_optional_nulls(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        wid, _, _, logs, arm = await workspace(client, database, settings, tenant, app)
        await ingest(database, tenant, wid)
        response = await client.post(
            settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
            headers=logs,
            json={
                "query": "StorageBlobLogs | summarize n=count()",
                "timespan": None,
                "workspaces": None,
            },
        )
        assert response.status_code == 200 and response.json()["tables"][0]["rows"] == [[3]], (
            response.text
        )
        response = await client.post(
            "/providers/Microsoft.ResourceGraph/resources",
            headers=arm,
            params={"api-version": "2024-04-01"},
            json={"query": "Resources | project id", "options": {"allowPartialScopes": False}},
        )
        assert response.status_code == 200, response.text
        response = await client.post(
            "/providers/Microsoft.ResourceGraph/resources",
            headers=arm,
            params={"api-version": "2024-04-01"},
            json={"query": "Resources | project id", "options": {"allowPartialScopes": True}},
        )
        assert response.status_code == 400, response.text


async def test_native_false_query_preferences(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        wid, _, _, logs, _ = await workspace(client, database, settings, tenant, app)
        await ingest(database, tenant, wid)
        response = await client.post(
            settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
            headers=logs | {
                "Prefer": "response-v1=true,include-statistics=false,include-render=false,wait=30"
            },
            json={"query": "StorageBlobLogs | summarize n=count()"},
        )
        assert response.status_code == 200 and response.json()["tables"][0]["rows"] == [[3]], (
            response.text
        )
        response = await client.post(
            settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
            headers=logs | {"Prefer": "include-statistics=true"},
            json={"query": "StorageBlobLogs"},
        )
        assert response.status_code == 400


def test_scalar_let_allocations_share_query_budget():
    query = "let a = strcat('xxxxxxxxxx', 'xxxxxxxxxx'); "
    previous = "a"
    for i in range(8):
        name = "v" + str(i)
        query += f"let {name} = strcat({previous}, {previous}); "
        previous = name
    query += "Resources | count"
    with pytest.raises(QueryError) as error:
        evaluate_query(
            parse_query(query, "loganalytics"),
            {"Resources": QueryResult([("n", "long")], [])},
            datetime.now(UTC),
            limits=QueryLimits(bytes=1000),
        )
    assert error.value.code == "QueryLimitExceeded"
