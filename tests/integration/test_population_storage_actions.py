import asyncio
import hashlib
from uuid import UUID, uuid4

import pytest

from mockazure.population.actions import PublicActions
from mockazure.population.catalog import get_adapter
from mockazure.population.identity import resolve_identity
from mockazure.population.transport import LocalTransport
from mockazure.population.types import AdapterContext, Mutation, ResourceQuery
from tests.integration.identity_support import identity_client
from tests.integration.test_content_storage import upload


@pytest.mark.parametrize("race_phase", ["create", "range"])
async def test_storage_creation_and_uploaded_bytes_use_public_rbac(
    database, settings, monkeypatch, race_phase
):
    async with identity_client(database, settings) as (client, tenant, *_):
        ctx = AdapterContext(database, settings, UUID(tenant))

        async def transport(self, context, identity):
            return LocalTransport(context, await resolve_identity(context, identity), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)

        async def apply(family, operation, body, **kwargs):
            return await asyncio.wait_for(
                get_adapter(family).apply(
                    ctx, [Mutation(operation=operation, logical_key=family, payload=body, **kwargs)]
                ),
                15,
            )

        await apply(
            "identity.clients", "provision", {"id": "cloud", "display_name": "Cloud test client"}
        )
        subscription = str(uuid4())
        scope = "/subscriptions/" + subscription
        await apply("arm.subscriptions", "import", {"id": subscription, "displayName": "Lab"})
        group = scope + "/resourceGroups/lab"
        await apply("arm.resourceGroups", "import", {"id": group, "location": "eastus"})
        for role in (
            "Contributor",
            "Storage Blob Data Contributor",
            "Storage Queue Data Contributor",
            "Storage Table Data Contributor",
            "Storage File Data Privileged Contributor",
        ):
            await apply(
                "arm.roles",
                "assign",
                {"id": str(uuid4()), "scope": scope, "role": role, "identity_ref": "cloud"},
            )
        await apply(
            "arm.providers",
            "register",
            {"variables": {"subscription": subscription, "namespace": "Microsoft.Storage"}},
            identity_ref="cloud",
        )
        account = group + "/providers/Microsoft.Storage/storageAccounts/populationaccount"
        result = await apply(
            "storage.accounts",
            "create",
            {
                "variables": {"resource": account},
                "body": {
                    "location": "eastus",
                    "kind": "StorageV2",
                    "sku": {"name": "Standard_LRS"},
                },
            },
            identity_ref="cloud",
        )
        assert (await get_adapter("storage.accounts").verify(ctx, result)).status == "succeeded"
        await apply(
            "storage.containers",
            "create",
            {"variables": {"account": "populationaccount", "container": "evidence"}},
            identity_ref="cloud",
        )
        probe = await LocalTransport(
            ctx, await resolve_identity(ctx, "cloud"), client=client
        ).request(
            "storage",
            "GET",
            "/populationaccount/evidence/sample.bin",
            headers={"x-ms-version": "2025-11-05"},
        )
        assert probe.status_code == 404, probe.text
        raw = b"binary\x00evidence\xff" * 1000
        content = await upload(database, UUID(tenant), raw)
        result = await apply(
            "storage.blobs",
            "upload",
            {
                "variables": {
                    "account": "populationaccount",
                    "container": "evidence",
                    "blob": "sample.bin",
                },
                "content_id": str(content.id),
            },
            identity_ref="cloud",
        )
        assert (await get_adapter("storage.blobs").verify(ctx, result)).status == "succeeded"
        credentials = await resolve_identity(ctx, "cloud")
        actual = await LocalTransport(ctx, credentials, client=client).request(
            "storage",
            "GET",
            "/populationaccount/evidence/sample.bin",
            headers={"x-ms-version": "2025-11-05"},
        )
        assert hashlib.sha256(actual.content).digest() == hashlib.sha256(raw).digest()
        page = await get_adapter("storage.blobs").browse(ctx, ResourceQuery())
        assert page.items[0].properties["name"] == "sample.bin"

        changed = await LocalTransport(ctx, credentials, client=client).request(
            "storage",
            "PUT",
            "/populationaccount/evidence/sample.bin",
            headers={"x-ms-version": "2025-11-05", "x-ms-blob-type": "BlockBlob"},
            content=b"changed",
        )
        assert changed.status_code == 201
        assert (await get_adapter("storage.blobs").verify(ctx, result)).status == "failed"
        await apply(
            "storage.queues",
            "create",
            {"variables": {"account": "populationaccount", "queue": "evidence"}},
            identity_ref="cloud",
        )
        queued = await apply(
            "storage.messages",
            "enqueue",
            {
                "variables": {"account": "populationaccount", "queue": "evidence"},
                "body": {"text": "queue <evidence>"},
            },
            identity_ref="cloud",
        )
        assert (await get_adapter("storage.messages").verify(ctx, queued)).status == "succeeded"
        table = await apply(
            "storage.tables",
            "create",
            {"variables": {"account": "populationaccount"}, "body": {"TableName": "Evidence"}},
            identity_ref="cloud",
        )
        assert (await get_adapter("storage.tables").verify(ctx, table)).status == "succeeded"
        entity = await apply(
            "storage.entities",
            "insert",
            {
                "variables": {"account": "populationaccount", "table": "Evidence"},
                "body": {"PartitionKey": "case", "RowKey": "1", "Count": 42},
            },
            identity_ref="cloud",
        )
        assert (await get_adapter("storage.entities").verify(ctx, entity)).status == "succeeded"
        await apply(
            "storage.shares",
            "create",
            {"variables": {"account": "populationaccount", "share": "evidence"}},
            identity_ref="cloud",
        )
        file = await apply(
            "storage.files",
            "upload",
            {
                "variables": {
                    "account": "populationaccount",
                    "share": "evidence",
                    "file": "sample.bin",
                },
                "content_id": str(content.id),
            },
            identity_ref="cloud",
        )
        assert (await get_adapter("storage.files").verify(ctx, file)).status == "succeeded"
        # Interleave a real external FileREST writer at both gaps in the upload.
        from mockazure.admin.errors import AdminError

        original_request = LocalTransport.request
        for phase in (race_phase,):
            race_path = "/populationaccount/evidence/race-" + phase + ".bin"
            injected = False
            external_bytes = b"other".ljust(len(raw), b"_")

            async def racing_request(self, service, method, path, **options):
                nonlocal injected
                is_range = (options.get("params") or {}).get("comp") == "range"
                if (
                    service == "file"
                    and method == "PUT"
                    and path == race_path
                    and is_range == (phase == "range")
                    and not injected
                ):
                    injected = True
                    h = {"x-ms-version": "2025-11-05", "x-ms-file-request-intent": "backup"}
                    created = await original_request(
                        self,
                        "file",
                        "PUT",
                        path,
                        headers={
                            **h,
                            "x-ms-type": "file",
                            "x-ms-content-length": str(len(external_bytes)),
                        },
                    )
                    assert created.status_code == 201, created.text
                    written = await original_request(
                        self,
                        "file",
                        "PUT",
                        path,
                        params={"comp": "range"},
                        headers={
                            **h,
                            "x-ms-range": f"bytes=0-{len(external_bytes) - 1}",
                            "x-ms-write": "update",
                        },
                        content=external_bytes,
                    )
                    assert written.status_code == 201, written.text
                return await original_request(self, service, method, path, **options)

            with monkeypatch.context() as patch:
                patch.setattr(LocalTransport, "request", racing_request)
                with pytest.raises(AdminError) as conflict:
                    await apply(
                        "storage.files",
                        "upload",
                        {
                            "variables": {
                                "account": "populationaccount",
                                "share": "evidence",
                                "file": "race-" + phase + ".bin",
                            },
                            "content_id": str(content.id),
                        },
                        identity_ref="cloud",
                        expected_version="absent",
                    )
                assert conflict.value.status == 412
            actual = await original_request(
                LocalTransport(ctx, credentials, client=client),
                "file",
                "GET",
                race_path,
                headers={"x-ms-version": "2025-11-05", "x-ms-file-request-intent": "backup"},
            )
            assert actual.content == external_bytes
