import json
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import select

from mockazure.cloud.models import CloudEvent
from tests.integration.graph_support import graph_client, headers_for, registered_app
from tests.integration.test_diagnostic_delivery import VERSION, drain, setup


async def failed_sources(database, tenant):
    async with database.sessions() as session:
        return list(
            await session.scalars(
                select(CloudEvent).where(
                    CloudEvent.tenant_id == tenant,
                    CloudEvent.payload["outcome"].as_string() == "Failed",
                )
            )
        )


async def all_services(client, database, settings, tenant, app):
    from tests.integration.test_keyvault_secrets import ARGS, provision_vault

    account, _, path, body, arm, endpoints, key, wid, logs = await setup(
        client, database, settings, tenant, app
    )
    body["properties"]["logs"] = [{"categoryGroup": "allLogs", "enabled": True}]
    for service in ("blob", "file", "table", "queue"):
        target = path.replace("/blobServices/", "/" + service + "Services/")
        response = await client.put(target, headers=arm, params=VERSION, json=body)
        assert response.status_code == 200, response.text
    vault, _, _, vault_arm, base, head, _ = await provision_vault(
        client, database, settings, tenant, app
    )
    response = await client.put(
        vault + "/providers/Microsoft.Insights/diagnosticSettings/failures",
        headers=vault_arm,
        params=VERSION,
        json=body,
    )
    assert response.status_code == 200, response.text
    # An unprivileged, but valid and local, application.
    other = await registered_app(client, tenant)
    storage_auth = await headers_for(
        database, settings, tenant, other, None, [], audience="https://storage.azure.com/"
    )
    vault_auth = await headers_for(
        database, settings, tenant, other, None, [], audience="https://vault.azure.net"
    )
    requests = [
        (
            "blob",
            endpoints["blob"] + "evidence/missing",
            storage_auth | {"x-ms-version": "2025-11-05"},
            {},
        ),
        (
            "file",
            endpoints["file"] + "evidence/missing",
            storage_auth | {"x-ms-version": "2025-11-05", "x-ms-file-request-intent": "backup"},
            {},
        ),
        (
            "table",
            endpoints["table"] + "Evidence()",
            storage_auth | {"x-ms-version": "2019-02-02", "Accept": "application/json"},
            {},
        ),
        (
            "queue",
            endpoints["queue"] + "evidence/messages",
            storage_auth | {"x-ms-version": "2026-02-06"},
            {"peekonly": "true"},
        ),
        ("keyvault", base + "secrets/secret-name", vault_auth, ARGS),
    ]
    return requests, (account, endpoints, key, wid, logs, base, head)


async def test_permission_denial_survives_mutation_rollback(database, settings):
    from mockazure.storage.blob_models import Blob

    async with graph_client(database, settings) as (client, tenant, app, _):
        requests, (_, _, _, wid, logs, _, _) = await all_services(
            client, database, settings, tenant, app
        )
        request_ids = set()
        for service, url, headers, params in requests:
            response = await client.get(url, headers=headers, params=params)
            assert response.status_code == 403, (service, response.text)
            request_ids.add(UUID(response.headers["x-ms-request-id"]))
        sources = await failed_sources(database, tenant)
        assert len(sources) == 5
        assert {UUID(s.payload["requestId"]) for s in sources} == request_ids
        assert {s.service for s in sources} == {"blob", "file", "table", "queue", "keyvault"}
        async with database.sessions() as session:
            assert list(await session.scalars(select(Blob).where(Blob.tenant_id == tenant))) == []
        # Dispose/reopen connections before delivery: sources and jobs must be durable.
        await database.engine.dispose()
        result = await drain(database, tenant)
        assert len(result) == 10 and all(r.state == "succeeded" for r in result)
        for table in [
            "StorageBlobLogs",
            "StorageFileLogs",
            "StorageTableLogs",
            "StorageQueueLogs",
            "AZKVAuditLogs",
        ]:
            response = await client.post(
                settings.endpoints.logs + f"/v1/workspaces/{wid}/query",
                headers=logs,
                json={"query": table},
            )
            assert response.status_code == 200, response.text
            assert len(response.json()["tables"][0]["rows"]) == 1
        from mockazure.content.repository import stream_content
        from mockazure.storage.blob_models import BlobVersion

        async with database.sessions() as session:
            for blob in await session.scalars(select(Blob).where(Blob.tenant_id == tenant)):
                version = await session.get(BlobVersion, (tenant, blob.current_id))
                raw = b"".join(
                    [p async for p in stream_content(session, tenant, version.content_id)]
                )
                for record in json.loads(raw)["records"]:
                    assert record["resultType"] == "Failed"
                    assert record["resultSignature"] == "403"


async def test_invalid_token_does_not_select_victim_tenant(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        requests, _ = await all_services(client, database, settings, tenant, app)
        for service, url, headers, params in requests:
            response = await client.get(
                url, headers=headers | {"Authorization": "Bearer invalid"}, params=params
            )
            assert response.status_code in {401, 403}, (service, response.text)
        assert await failed_sources(database, tenant) == []


async def test_failure_capture_does_not_replace_public_error(database, settings):
    from mockazure.evidence import failures

    async with graph_client(database, settings) as (client, tenant, app, _):
        requests, _ = await all_services(client, database, settings, tenant, app)
        with patch.object(failures, "capture_failure", side_effect=OSError("private-data")):
            for service, url, headers, params in requests:
                response = await client.get(url, headers=headers, params=params)
                assert response.status_code == 403, (service, response.text)
                assert "private-data" not in response.text
        assert await failed_sources(database, tenant) == []


async def test_known_resource_validation_failure_is_logged(database, settings):
    from tests.integration.test_keyvault_secrets import ARGS

    async with graph_client(database, settings) as (client, tenant, app, _):
        _, (_, _, _, _, _, base, head) = await all_services(client, database, settings, tenant, app)
        response = await client.put(
            base + "secrets/invalid",
            params=ARGS,
            headers=head,
            json={"value": "NEVER-EXPORT-THIS", "attributes": {"enabled": "not-a-bool"}},
        )
        assert response.status_code == 400
        rows = await failed_sources(database, tenant)
        assert len(rows) == 1 and rows[0].operation == "SecretSet"
        assert rows[0].payload["statusCode"] == 400
        assert "NEVER-EXPORT-THIS" not in json.dumps(rows[0].payload)


async def test_foreign_resource_denial_does_not_disclose_resource(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        requests, _ = await all_services(client, database, settings, tenant, app)
        response = await client.post(
            "/_mock/tenants",
            json={
                "slug": "foreign",
                "display_name": "Foreign",
                "domain": "foreign.test",
                "licences": [],
            },
        )
        assert response.status_code == 201, response.text
        foreign = UUID(response.json()["id"])
        other = await registered_app(client, foreign)
        for service, url, head, params in requests:
            token = await headers_for(
                database,
                settings,
                foreign,
                other,
                None,
                [],
                audience="https://vault.azure.net"
                if service == "keyvault"
                else "https://storage.azure.com/",
            )
            response = await client.get(url, headers=head | token, params=params)
            assert response.status_code == 403, (service, response.text)
        assert await failed_sources(database, tenant) == []
        assert await failed_sources(database, foreign) == []


async def test_signed_sas_denial_is_attributed_without_signature_material(database, settings):
    from tests.integration.test_storage_authorization import account_sas

    async with graph_client(database, settings) as (client, tenant, app, _):
        _, (_, endpoints, key, _, _, _, _) = await all_services(
            client, database, settings, tenant, app
        )
        query = account_sas(key, sp="r") | {"restype": "container"}
        client.headers.pop("Authorization", None)
        response = await client.put(endpoints["blob"] + "denied", params=query)
        assert response.status_code == 403
        rows = await failed_sources(database, tenant)
        assert len(rows) == 1
        raw = json.dumps(rows[0].payload)
        assert query["sig"] not in raw and key not in raw
        assert rows[0].payload["authenticationType"] == "account_sas"


async def test_one_request_has_one_native_failure_fact(database, settings):
    from datetime import UTC, datetime
    from uuid import uuid4

    from mockazure.evidence.failure_models import FailureFact
    from mockazure.evidence.failures import capture_failure

    async with graph_client(database, settings) as (client, tenant, app, _):
        _, (account, _, _, _, _, _, _) = await all_services(client, database, settings, tenant, app)
        failure = FailureFact(
            tenant_id=tenant,
            source_resource_id=account,
            service="blob",
            operation_key="GetBlob",
            request_id=uuid4(),
            occurred_at=datetime.now(UTC),
            principal_id=None,
            status_code=412,
            error_code="ConditionNotMet",
            safe_properties={"authenticationType": "shared_key"},
        )
        first = await capture_failure(database, failure)
        second = await capture_failure(database, failure)
        rows = await failed_sources(database, tenant)
        assert first == second and [row.id for row in rows] == [first]
        assert len(await drain(database, tenant)) == 2


async def test_failed_table_batch_rolls_back_writes_but_preserves_failure(database, settings):
    from tests.integration.test_table_workflows import batch, table_call

    async with graph_client(database, settings) as (client, tenant, app, _):
        _, (_, endpoints, key, _, _, _, _) = await all_services(
            client, database, settings, tenant, app
        )
        base = endpoints["table"]
        assert (
            await table_call(client, "POST", base + "Tables", key, body={"TableName": "Evidence"})
        ).status_code == 201
        operations = [
            ("POST", "Evidence", {"PartitionKey": "p", "RowKey": "a"}, None),
            (
                "PUT",
                "Evidence(PartitionKey='p',RowKey='b')",
                {"PartitionKey": "p", "RowKey": "b"},
                "*",
            ),
        ]
        response = await table_call(
            client,
            "POST",
            base + "$batch",
            key,
            raw=batch(base, operations),
            headers={"Content-Type": "multipart/mixed; boundary=batch_test"},
        )
        assert response.status_code == 202 and "404" in response.text, response.text
        assert (await table_call(client, "GET", base + "Evidence()", key)).json()["value"] == []
        rows = await failed_sources(database, tenant)
        assert len(rows) == 1 and rows[0].operation == "UpdateEntity"
