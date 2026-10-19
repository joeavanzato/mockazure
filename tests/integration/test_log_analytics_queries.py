"""Real signed Logs tokens, PostgreSQL rows and current workspace RBAC."""

from uuid import UUID

from sqlalchemy import select

from mockazure.providers.arm.models import RoleAssignment
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_cloud_resource_inventory import VERSIONS, create, prepare


async def workspace(client, database, settings, tenant, app, name="logs"):
    sub, base, group, principal, arm = await prepare(client, database, settings, tenant, app)
    path = group + "/providers/Microsoft.OperationalInsights/workspaces/" + name
    result = await create(
        client,
        path,
        {"location": "eastus", "properties": {"sku": {"name": "PerGB2018"}, "retentionInDays": 30}},
        arm,
        VERSIONS["Microsoft.OperationalInsights"],
    )
    logs = await headers_for(
        database, settings, tenant, app, None, set(), audience="https://api.loganalytics.io"
    )
    return UUID(result["properties"]["customerId"]), base, principal, logs, arm


async def ingest(database, tenant, workspace_id):
    from mockazure.monitor.logs import ingest_log_rows

    rows = [
        {
            "TimeGenerated": "2026-10-01T12:00:00Z",
            "OperationName": "GetBlob",
            "DurationMs": 2.0,
            "AccountName": "local",
            "AuthorizationDetails": {"allowed": True},
        },
        {
            "TimeGenerated": "2026-10-02T12:00:00Z",
            "OperationName": "GetBlob",
            "DurationMs": 4.0,
            "AccountName": "local",
            "AuthorizationDetails": {"allowed": True},
        },
        {
            "TimeGenerated": "2026-10-03T12:00:00Z",
            "OperationName": "PutBlob",
            "DurationMs": 8.0,
            "AccountName": "local",
            "AuthorizationDetails": {"allowed": False},
        },
    ]
    async with database.sessions.begin() as session:
        assert (
            await ingest_log_rows(
                session, tenant, workspace_id, "StorageBlobLogs", rows, ["a", "b", "c"]
            )
            == 3
        )
        assert (
            await ingest_log_rows(
                session, tenant, workspace_id, "StorageBlobLogs", rows, ["a", "b", "c"]
            )
            == 0
        )


async def test_logs_tables_and_timespan(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        wid, base, principal, logs, arm = await workspace(client, database, settings, tenant, app)
        await ingest(database, tenant, wid)
        url = settings.endpoints.logs + f"/v1/workspaces/{wid}"
        query = (
            "let read = StorageBlobLogs | where tobool(AuthorizationDetails.allowed); "
            "read | summarize calls=count(), total=sum(DurationMs) by AccountName "
            "| project AccountName, calls, total"
        )
        response = await client.post(
            url + "/query",
            params={"timespan": "2026-10-02T00:00:00Z/2026-10-04T00:00:00Z"},
            headers=logs,
            json={"query": query, "timespan": "2026-10-01T00:00:00Z/2026-10-03T00:00:00Z"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["tables"] == [
            {
                "name": "PrimaryResult",
                "columns": [
                    {"name": "AccountName", "type": "string"},
                    {"name": "calls", "type": "long"},
                    {"name": "total", "type": "real"},
                ],
                "rows": [["local", 1, 4.0]],
            }
        ]
        metadata = await client.get(url + "/metadata", headers=logs)
        assert metadata.status_code == 200, metadata.text
        table = next(t for t in metadata.json()["tables"] if t["name"] == "StorageBlobLogs")
        assert {"name": "TimeGenerated", "type": "datetime"} in table["columns"]
        assert {"name": "AuthorizationDetails", "type": "dynamic"} in table["columns"]
        response = await client.get(
            url + "/query",
            headers=logs,
            params={
                "query": "StorageBlobLogs | project TimeGenerated, OperationName "
                "| order by TimeGenerated asc"
            },
        )
        assert response.status_code == 200 and len(response.json()["tables"][0]["rows"]) == 3
        assert response.json()["tables"][0]["rows"][0][0] == "2026-10-01T12:00:00Z"
        invalid = await client.post(url + "/query", headers=arm, json={"query": "StorageBlobLogs"})
        assert (
            invalid.status_code == 401
            and "https://api.loganalytics.io" in invalid.headers["www-authenticate"]
        )
        assert (
            await client.get(
                url + "/query", headers=logs, params={"query": "StorageBlobLogs | project fake"}
            )
        ).status_code == 400


async def test_logs_cross_workspace_auth(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        first, base, principal, logs, _ = await workspace(client, database, settings, tenant, app)
        second, base2, _, _, _ = await workspace(client, database, settings, tenant, app, "other")
        await ingest(database, tenant, first)
        await ingest(database, tenant, second)
        url = settings.endpoints.logs + f"/v1/workspaces/{first}/query"
        body = {"query": "StorageBlobLogs | summarize n=count()", "workspaces": [str(second)]}
        response = await client.post(url, headers=logs, json=body)
        assert response.status_code == 200 and response.json()["tables"][0]["rows"] == [[6]], (
            response.text
        )
        async with database.sessions() as session:
            grants = list(
                await session.scalars(
                    select(RoleAssignment.id).where(
                        RoleAssignment.tenant_id == tenant,
                        RoleAssignment.subscription_id == UUID(base2.rsplit("/", 1)[1]),
                    )
                )
            )
        for grant in grants:
            assert (
                await client.delete(base2 + "/role-assignments/" + str(grant))
            ).status_code == 200
        denied = await client.post(url, headers=logs, json=body)
        assert denied.status_code == 403 and "tables" not in denied.json(), denied.text
        current = await client.post(url, headers=logs, json={"query": body["query"]})
        assert current.status_code == 200 and current.json()["tables"][0]["rows"] == [[3]]


async def test_logs_batch_mixed_errors(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        wid, base, principal, logs, _ = await workspace(client, database, settings, tenant, app)
        await ingest(database, tenant, wid)
        requests = [
            {
                "id": name,
                "method": "POST",
                "path": "/query",
                "workspace": str(wid),
                "body": {"query": query},
            }
            for name, query in [
                ("good", "StorageBlobLogs | count"),
                ("bad", "StorageBlobLogs | project fake"),
            ]
        ]
        response = await client.post(
            settings.endpoints.logs + "/v1/$batch", headers=logs, json={"requests": requests}
        )
        assert response.status_code == 200, response.text
        results = response.json()["responses"]
        assert [r["id"] for r in results] == ["good", "bad"]
        assert [r["status"] for r in results] == [200, 400]
        assert results[0]["body"]["tables"][0]["rows"] == [[3]]
        assert results[1]["body"]["error"]["code"] == "BadArgumentError"


async def test_logs_import_atomicity_and_lifecycle(database, settings):
    import copy
    from datetime import UTC, datetime

    import pytest
    from sqlalchemy import func, select

    from mockazure.monitor.lifecycle import prune_logs
    from mockazure.monitor.log_models import LogRecord
    from mockazure.monitor.logs import ingest_log_rows

    async with graph_client(database, settings) as (client, tenant, app, _):
        wid, base, principal, logs, _ = await workspace(client, database, settings, tenant, app)
        await ingest(database, tenant, wid)
        async with database.sessions.begin() as session:
            with pytest.raises(ValueError):
                await ingest_log_rows(
                    session,
                    tenant,
                    wid,
                    "StorageBlobLogs",
                    [
                        {"TimeGenerated": "2026-10-01T12:00:00Z", "OperationName": "new"},
                        {"TimeGenerated": "2026-10-01T12:00:00Z", "OperationName": "conflict"},
                    ],
                    ["new", "a"],
                )
            assert (
                await session.scalar(
                    select(func.count()).select_from(LogRecord).where(LogRecord.tenant_id == tenant)
                )
                == 3
            )
        admin = f"/_mock/tenants/{tenant}/cloud"
        value = (await client.get(admin + "/state")).json()
        assert len(value["logs"]["records"]) == 3
        invalid = copy.deepcopy(value)
        invalid["logs"]["records"][0]["payload"]["DurationMs"] = "not-a-number"
        assert (await client.put(admin + "/state", json=invalid)).status_code == 422
        reset = await client.post(
            admin + "/reset", json={"expected_generation": value["generation"]}
        )
        assert reset.status_code == 200 and reset.json()["logs"]["records"] == [], reset.text
        restored = await client.put(admin + "/state", json=value)
        assert restored.status_code == 200 and len(restored.json()["logs"]["records"]) == 3, (
            restored.text
        )
        async with database.sessions.begin() as session:
            assert await prune_logs(session, tenant, datetime(2026, 12, 1, tzinfo=UTC), 2) == 2
            assert (
                await session.scalar(
                    select(func.count()).select_from(LogRecord).where(LogRecord.tenant_id == tenant)
                )
                == 1
            )
        imported = await client.post(
            admin + f"/workspaces/{wid}/logs/StorageBlobLogs",
            json={
                "rows": [{"TimeGenerated": "2026-12-01T00:00:00Z", "OperationName": "Imported"}],
                "source_ids": ["admin-record"],
            },
        )
        assert imported.status_code == 200 and imported.json() == {"inserted": 1}, imported.text
        spoofed = await client.post(
            admin + f"/workspaces/{wid}/logs/StorageBlobLogs",
            json={"rows": [], "source_ids": [], "origin": "native"},
        )
        assert spoofed.status_code == 422


async def test_logs_delegated_scope_and_all_table_contracts(database, settings):
    from mockazure.monitor.tables import catalog
    from mockazure.providers.arm.contracts import validate_arm_payload
    from tests.integration.test_arm_authorization import assign

    async with graph_client(database, settings) as (client, tenant, app, user):
        wid, base, principal, logs, _ = await workspace(client, database, settings, tenant, app)
        await assign(
            client, base, user, "/subscriptions/" + base.rsplit("/", 1)[1], "Reader", kind="User"
        )
        good = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"Data.Read"},
            kind="delegated",
            audience="https://api.loganalytics.io",
        )
        bad = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"user_impersonation"},
            kind="delegated",
            audience="https://api.loganalytics.io",
        )
        url = settings.endpoints.logs + f"/v1/workspaces/{wid}/query"
        assert (
            await client.post(url, headers=bad, json={"query": "StorageBlobLogs | count"})
        ).status_code == 403
        admin = f"/_mock/tenants/{tenant}/cloud/workspaces/{wid}/logs/"
        for table in catalog():
            row = {"TimeGenerated": "2026-10-01T00:00:00Z"}
            imported = await client.post(
                admin + table, json={"rows": [row], "source_ids": ["all-tables"]}
            )
            assert imported.status_code == 200, imported.text
            response = await client.post(
                url,
                headers=good,
                json={"query": table + " | project TimeGenerated, TenantId, Type"},
            )
            assert response.status_code == 200, response.text
            validate_arm_payload("QueryResults", response.json(), namespace="loganalytics")
            assert response.json()["tables"][0]["rows"] == [
                ["2026-10-01T00:00:00Z", str(wid), table]
            ]
        for query in [
            "StorageBlobLogs | project missing",
            "StorageBlobLogs; DROP TABLE monitor_log_records",
            "workspace('external').StorageBlobLogs",
        ]:
            response = await client.post(url, headers=good, json={"query": query})
            assert (
                response.status_code == 400
                and response.json()["error"]["code"] == "BadArgumentError"
            )


async def test_logs_oauth_client_credentials(database, settings):
    from tests.integration.identity_support import identity_client

    async with identity_client(database, settings) as (client, tenant, app, credential, *unused):
        response = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": str(app),
                "client_secret": credential["secret"],
                "scope": "https://api.loganalytics.io/.default",
            },
        )
        assert response.status_code == 200, response.text
