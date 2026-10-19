"""Diagnostic configuration must cause durable local service events to reach real sinks."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from mockazure.cloud.models import CloudEvent
from mockazure.jobs.dispatch import dispatch_job
from mockazure.jobs.repository import claim_job
from mockazure.monitor.workspaces import Workspace
from tests.integration.graph_support import graph_client
from tests.integration.test_blob_workflows import call
from tests.integration.test_log_analytics_queries import workspace
from tests.integration.test_storage_authorization import provision

VERSION = {"api-version": "2021-05-01-preview"}


async def setup(client, database, settings, tenant, app):
    account, arm, endpoints, key = await provision(client, database, settings, tenant, app)
    wid, _, _, logs, _ = await workspace(client, database, settings, tenant, app)
    async with database.sessions() as session:
        target = await session.scalar(
            select(Workspace.scope_key).where(
                Workspace.tenant_id == tenant, Workspace.customer_id == wid
            )
        )
    source = account + "/blobServices/default"
    path = source + "/providers/Microsoft.Insights/diagnosticSettings/collector"
    body = {
        "properties": {
            "workspaceId": target,
            "storageAccountId": account,
            "logAnalyticsDestinationType": "Dedicated",
            "logs": [{"category": "StorageWrite", "enabled": True}],
        }
    }
    return account, source, path, body, arm, endpoints, key, wid, logs


async def drain(database, tenant, now=None):
    now = now or datetime.now(UTC)
    results = []
    for _ in range(30):
        async with database.sessions.begin() as session:
            claim = await claim_job(
                session,
                owner=uuid4(),
                now=now,
                lease_seconds=60,
                tenant_id=tenant,
                kind="monitor.diagnostic",
            )
        if claim is None:
            return results
        results.append(await dispatch_job(database, claim, now))
    raise AssertionError("Unexpected diagnostic feedback loop")


async def test_storage_keyvault_diagnostics(database, settings):
    from mockazure.providers.arm.contracts import validate_arm_payload
    from tests.integration.test_arm_authorization import assign
    from tests.integration.test_keyvault_secrets import ARGS, provision_vault

    async with graph_client(database, settings) as (client, tenant, app, _):
        account, source, path, body, arm, endpoints, key, wid, logs = await setup(
            client, database, settings, tenant, app
        )
        categories = await client.get(
            source + "/providers/Microsoft.Insights/diagnosticSettingsCategories",
            headers=arm,
            params={"api-version": "2017-05-01-preview"},
        )
        assert categories.status_code == 200, categories.text
        validate_arm_payload(
            "DiagnosticSettingsCategoryResourceCollection",
            categories.json(),
            namespace="diagnosticCategories",
        )
        modern = await client.get(
            source + "/providers/Microsoft.Insights/diagnosticSettingsCategories",
            headers=arm,
            params={"api-version": "2021-05-01-preview"},
        )
        assert modern.status_code == 200, modern.text
        validate_arm_payload(
            "DiagnosticSettingsCategoryResourceCollection",
            modern.json(),
            namespace="diagnosticCategories2021",
        )
        assert {v["name"] for v in categories.json()["value"]} == {
            "StorageRead",
            "StorageWrite",
            "StorageDelete",
        }
        configured = await client.put(path, headers=arm, params=VERSION, json=body)
        assert configured.status_code == 200, configured.text
        validate_arm_payload(
            "DiagnosticSettingsResource", configured.json(), namespace="diagnostics"
        )
        wrong = await client.put(
            path,
            headers=arm,
            params=VERSION,
            json={
                "properties": body["properties"]
                | {"logs": [{"category": "Imaginary", "enabled": True}]}
            },
        )
        assert wrong.status_code == 400
        assert (
            await call(client, "PUT", endpoints["blob"] + "evidence", key, {"restype": "container"})
        ).status_code == 201
        assert (
            await call(
                client,
                "PUT",
                endpoints["blob"] + "evidence/record",
                key,
                body=b"bytes",
                extra={"x-ms-blob-type": "BlockBlob"},
            )
        ).status_code == 201
        vault, admin, principal, _, vault_base, vault_head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, vault, "Key Vault Secrets Officer")
        value = {
            "properties": body["properties"]
            | {"logs": [{"category": "AuditEvent", "enabled": True}]}
        }
        assert (
            await client.put(
                vault + "/providers/Microsoft.Insights/diagnosticSettings/audit",
                headers=arm,
                params=VERSION,
                json=value,
            )
        ).status_code == 200
        secret = "diagnostic-secret-must-not-leak"
        assert (
            await client.put(
                vault_base + "secrets/evidence",
                params=ARGS,
                headers=vault_head,
                json={"value": secret},
            )
        ).status_code == 200
        results = await drain(database, tenant)
        assert len(results) == 6 and all(r.state == "succeeded" for r in results), results
        url = settings.endpoints.logs + f"/v1/workspaces/{wid}/query"
        response = await client.post(
            url,
            headers=logs,
            json={"query": "StorageBlobLogs | project OperationName | order by OperationName asc"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["tables"][0]["rows"] == [["CreateContainer"], ["PutBlob"]]
        response = await client.post(
            url, headers=logs, json={"query": "AZKVAuditLogs | project OperationName, _ResourceId"}
        )
        assert response.status_code == 200 and response.json()["tables"][0]["rows"] == [
            ["SecretSet", vault.lower()]
        ], response.text
        from mockazure.content.repository import stream_content
        from mockazure.storage.blob_models import Blob, BlobVersion

        async with database.sessions() as session:
            blobs = list(
                await session.scalars(
                    select(Blob).where(Blob.tenant_id == tenant, Blob.container.like("insights-%"))
                )
            )
            assert len(blobs) == 2
            for blob in blobs:
                assert blob.name.endswith("/m=00/PT1H.json")
                version = await session.get(BlobVersion, (tenant, blob.current_id))
                raw = b"".join(
                    [chunk async for chunk in stream_content(session, tenant, version.content_id)]
                )
                assert secret.encode() not in raw and key.encode() not in raw
                assert json.loads(raw)["records"]


async def test_diagnostic_retry_and_changes(database, settings):
    from unittest.mock import patch

    from mockazure.monitor import publication

    async with graph_client(database, settings) as (client, tenant, app, _):
        _, _, path, body, arm, endpoints, key, wid, logs = await setup(
            client, database, settings, tenant, app
        )
        assert (await client.put(path, headers=arm, params=VERSION, json=body)).status_code == 200
        assert (
            await call(client, "PUT", endpoints["blob"] + "retry", key, {"restype": "container"})
        ).status_code == 201
        now = datetime.now(UTC)
        with patch.object(
            publication, "deliver_blob", side_effect=OSError("local temporary fault")
        ):
            results = await drain(database, tenant, now)
        assert sorted(r.state for r in results) == ["pending", "succeeded"]
        # Changing categories affects future source events, not captured destination work.
        changed = {
            "properties": body["properties"]
            | {"logs": [{"category": "StorageDelete", "enabled": True}]}
        }
        assert (
            await client.put(path, headers=arm, params=VERSION, json=changed)
        ).status_code == 200
        results = await drain(database, tenant, now + timedelta(minutes=1))
        assert len(results) == 1 and results[0].state == "succeeded"
        assert (
            await call(client, "PUT", endpoints["blob"] + "ignored", key, {"restype": "container"})
        ).status_code == 201
        assert await drain(database, tenant) == []
        assert (await client.put(path, headers=arm, params=VERSION, json=body)).status_code == 200
        assert (
            await call(client, "PUT", endpoints["blob"] + "removed", key, {"restype": "container"})
        ).status_code == 201
        assert (await client.delete(path, headers=arm, params=VERSION)).status_code == 204
        results = await drain(database, tenant)
        assert len(results) == 2 and all(r.state == "failed" for r in results)
        from mockazure.monitor.diagnostic_lifecycle import prune_diagnostics

        async with database.sessions.begin() as session:
            removed = [
                await prune_diagnostics(session, tenant, now + timedelta(days=31), 1)
                for _ in range(6)
            ]
            assert removed == [1, 1, 1, 1, 1, 0]


async def test_diagnostic_no_feedback(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, _, path, body, arm, endpoints, key, wid, logs = await setup(
            client, database, settings, tenant, app
        )
        assert (await client.put(path, headers=arm, params=VERSION, json=body)).status_code == 200
        assert (
            await call(client, "PUT", endpoints["blob"] + "source", key, {"restype": "container"})
        ).status_code == 201
        async with database.sessions() as session:
            before = await session.scalar(
                select(func.count()).select_from(CloudEvent).where(CloudEvent.tenant_id == tenant)
            )
        assert len(await drain(database, tenant)) == 2
        assert await drain(database, tenant) == []
        async with database.sessions() as session:
            after = await session.scalar(
                select(func.count()).select_from(CloudEvent).where(CloudEvent.tenant_id == tenant)
            )
        assert before == after


async def test_diagnostic_snapshot_and_storage_reads(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, _, path, body, arm, endpoints, key, wid, logs = await setup(
            client, database, settings, tenant, app
        )
        body["properties"]["logs"] = [{"categoryGroup": "allLogs", "enabled": True}]
        assert (await client.put(path, headers=arm, params=VERSION, json=body)).status_code == 200
        assert (
            await call(client, "PUT", endpoints["blob"] + "state", key, {"restype": "container"})
        ).status_code == 201
        admin = f"/_mock/tenants/{tenant}/cloud"
        exported = (await client.get(admin + "/state")).json()
        assert len(exported["diagnostics"]["settings"]) == 1
        assert len(exported["diagnostics"]["deliveries"]) == 2
        ids = {d["source_id"] for d in exported["diagnostics"]["deliveries"]}
        reset = await client.post(
            admin + "/reset", json={"expected_generation": exported["generation"]}
        )
        assert reset.status_code == 200 and reset.json()["diagnostics"]["settings"] == []
        assert await drain(database, tenant) == []
        restored = await client.put(admin + "/state", json=exported)
        assert restored.status_code == 200, restored.text
        results = await drain(database, tenant)
        assert len(results) == 2 and all(r.state == "succeeded" for r in results), results
        assert {r.result["sourceId"] for r in results} == ids
        # Cloud restore intentionally rotates Storage keys; use the current key.
        from tests.integration.test_storage_authorization import ARGS as STORAGE_ARGS

        keys = await client.post(
            body["properties"]["storageAccountId"] + "/listKeys", headers=arm, params=STORAGE_ARGS
        )
        assert keys.status_code == 200, keys.text
        key = keys.json()["keys"][0]["value"]
        # Public reads produce their own read category; internal archive writes do not.
        assert (
            await call(
                client,
                "PUT",
                endpoints["blob"] + "state/read",
                key,
                body=b"read-me",
                extra={"x-ms-blob-type": "BlockBlob"},
            )
        ).status_code == 201
        assert (
            await call(client, "GET", endpoints["blob"] + "state/read", key)
        ).content == b"read-me"
        results = await drain(database, tenant)
        assert len(results) == 4 and all(r.state == "succeeded" for r in results)
        response = await client.post(
            settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
            headers=logs,
            json={
                "query": "StorageBlobLogs | where Category == 'StorageRead' | project OperationName"
            },
        )
        assert response.status_code == 200 and response.json()["tables"][0]["rows"] == [
            ["GetBlob"]
        ], response.text


async def test_file_queue_table_and_subscription_diagnostics(database, settings):
    from tests.integration.test_files_rest_workflows import create_file
    from tests.integration.test_queue_workflows import queue_call
    from tests.integration.test_table_workflows import table_call

    async with graph_client(database, settings) as (client, tenant, app, _):
        account, _, _, body, arm, endpoints, key, wid, logs = await setup(
            client, database, settings, tenant, app
        )
        props = {
            "workspaceId": body["properties"]["workspaceId"],
            "logAnalyticsDestinationType": "Dedicated",
            "logs": [{"categoryGroup": "allLogs", "enabled": True}],
        }
        for service in ("file", "queue", "table"):
            path = (
                account
                + f"/{service}Services/default/providers/Microsoft.Insights/diagnosticSettings/logs"
            )
            assert (
                await client.put(path, headers=arm, params=VERSION, json={"properties": props})
            ).status_code == 200
        file = endpoints["file"] + "files"
        assert (await call(client, "PUT", file, key, {"restype": "share"})).status_code == 201
        assert (await create_file(client, file + "/empty", key, 0)).status_code == 201
        assert (await call(client, "GET", file + "/empty", key)).status_code == 200
        queue = endpoints["queue"] + "events"
        assert (await queue_call(client, "PUT", queue, key)).status_code == 201
        assert (
            await queue_call(client, "GET", queue, key, {"comp": "metadata"})
        ).status_code == 200
        table = endpoints["table"] + "Tables"
        assert (
            await table_call(client, "POST", table, key, body={"TableName": "Evidence"})
        ).status_code == 201
        assert (await table_call(client, "GET", table, key)).status_code == 200
        results = await drain(database, tenant)
        assert results and all(r.state == "succeeded" for r in results), results
        for table in ("StorageFileLogs", "StorageQueueLogs", "StorageTableLogs"):
            response = await client.post(
                settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
                headers=logs,
                json={"query": table + " | where Category == 'StorageRead' | count"},
            )
            assert response.status_code == 200 and response.json()["tables"][0]["rows"] == [[1]], (
                response.text
            )
        subscription = "/".join(account.split("/")[:3])
        path = subscription + "/providers/Microsoft.Insights/diagnosticSettings/activity"
        response = await client.put(
            path,
            headers=arm,
            params=VERSION,
            json={
                "properties": {
                    "workspaceId": props["workspaceId"],
                    "logs": [{"category": "Administrative", "enabled": True}],
                }
            },
        )
        assert response.status_code == 200, response.text
        changed = await client.put(
            subscription + "/resourceGroups/recorded",
            headers=arm,
            params={"api-version": "2021-04-01"},
            json={"location": "eastus"},
        )
        assert changed.status_code in {200, 201}, changed.text
        results = await drain(database, tenant)
        assert results and all(r.state == "succeeded" for r in results), results
        response = await client.post(
            settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
            headers=logs,
            json={
                "query": "AzureActivity | where OperationNameValue == "
                "'Microsoft.Resources/subscriptions/resourceGroups/write' | count"
            },
        )
        assert response.status_code == 200 and response.json()["tables"][0]["rows"] == [[1]], (
            response.text
        )


async def test_diagnostic_permissions_and_restore_rejects_wrong_source(database, settings):
    import copy

    from tests.integration.graph_support import headers_for
    from tests.integration.test_arm_authorization import assign

    async with graph_client(database, settings) as (client, tenant, app, user):
        account, source, path, body, arm, endpoints, key, wid, logs = await setup(
            client, database, settings, tenant, app
        )
        subscription = account.split("/")[2]
        await assign(
            client,
            f"/_mock/tenants/{tenant}/subscriptions/{subscription}",
            user,
            account,
            "Reader",
            kind="User",
        )
        reader = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"user_impersonation"},
            kind="delegated",
            audience="https://management.azure.com/",
        )
        denied = await client.put(path, headers=reader, params=VERSION, json=body)
        assert denied.status_code == 403, denied.text
        assert (await client.put(path, headers=arm, params=VERSION, json=body)).status_code == 200
        assert (
            await call(client, "PUT", endpoints["blob"] + "bound", key, {"restype": "container"})
        ).status_code == 201
        admin = f"/_mock/tenants/{tenant}/cloud"
        before = (await client.get(admin + "/state")).json()
        bad = copy.deepcopy(before)
        bad["diagnostics"]["deliveries"][0]["event"]["resource_id"] = source.replace(
            "/blobServices/", "/fileServices/"
        ).lower()
        response = await client.put(admin + "/state", json=bad)
        assert response.status_code == 422, response.text
        after = (await client.get(admin + "/state")).json()
        assert after["diagnostics"] == before["diagnostics"]
        assert after["logs"] == before["logs"]
