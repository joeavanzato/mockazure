"""Public data mutations become queryable evidence and retain current authorization."""

from sqlalchemy import select

from mockazure.providers.arm.models import RoleAssignment
from tests.integration.graph_support import graph_client
from tests.integration.test_blob_workflows import call
from tests.integration.test_diagnostic_delivery import VERSION, drain, setup


async def test_workflow_evidence_composition(database, settings):
    from tools.benchmark_cloud_workflows import dataset_counts

    assert sum(dataset_counts(100000).values()) == 100000
    async with graph_client(database, settings) as (client, tenant, app, _):
        account, _, path, body, arm, endpoints, key, wid, logs = await setup(
            client, database, settings, tenant, app
        )
        assert (await client.put(path, headers=arm, params=VERSION, json=body)).status_code == 200
        assert (
            await call(
                client, "PUT", endpoints["blob"] + "composition", key, {"restype": "container"}
            )
        ).status_code == 201
        assert (
            await call(
                client,
                "PUT",
                endpoints["blob"] + "composition/evidence",
                key,
                body=b"composition",
                extra={"x-ms-blob-type": "BlockBlob"},
            )
        ).status_code == 201
        results = await drain(database, tenant)
        assert len(results) == 4 and all(r.state == "succeeded" for r in results)
        url = settings.endpoints.logs + f"/v1/workspaces/{wid}/query"
        query = {
            "query": "StorageBlobLogs | where OperationName == 'PutBlob' | project CorrelationId"
        }
        response = await client.post(url, headers=logs, json=query)
        assert response.status_code == 200 and len(response.json()["tables"][0]["rows"]) == 1
        snapshot = (await client.get(f"/_mock/tenants/{tenant}/cloud/state")).json()
        assert any(
            d["source_id"] == response.json()["tables"][0]["rows"][0][0]
            and d["state"] == "succeeded"
            for d in snapshot["diagnostics"]["deliveries"]
        )
        async with database.sessions() as session:
            grants = list(
                await session.scalars(
                    select(RoleAssignment).where(RoleAssignment.tenant_id == tenant)
                )
            )
        for grant in grants:
            removed = await client.delete(
                f"/_mock/tenants/{tenant}/subscriptions/{grant.subscription_id}/role-assignments/{grant.id}"
            )
            assert removed.status_code == 200
        assert (await client.post(url, headers=logs, json=query)).status_code == 403
        assert (await client.put(path, headers=arm, params=VERSION, json=body)).status_code == 403
