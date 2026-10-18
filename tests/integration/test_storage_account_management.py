"""Storage management uses persisted inventory, isolated keys and observed networks."""

import json
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.models import ArmResource
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_cloud_provider_registry import context

ARGS = {"api-version": "2025-06-01"}
CREATE = {"location": "eastus", "kind": "StorageV2", "sku": {"name": "Standard_LRS"}}


async def setup(client, database, settings, tenant, app):
    sub, base, group, principal, headers = await context(client, database, settings, tenant, app)
    await assign(client, base, principal, f"/subscriptions/{sub}", "Contributor")
    registered = await client.post(
        f"/subscriptions/{sub}/providers/Microsoft.Storage/register",
        params={"api-version": "2021-04-01"},
        headers=headers,
    )
    assert registered.status_code == 200, registered.text
    return sub, base, group, principal, headers


async def test_account_lifecycle_and_keys(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await setup(client, database, settings, tenant, app)
        path = group + "/providers/Microsoft.Storage/storageAccounts/evidenceaccount"
        response = await client.put(path, params=ARGS, headers=headers, json=CREATE)
        assert response.status_code == 200, response.text
        created = response.json()
        validate_arm_payload("StorageAccount", created, namespace="storage")
        assert created["name"] == "evidenceaccount"
        keys = (await client.post(path + "/listKeys", params=ARGS, headers=headers)).json()
        validate_arm_payload("StorageAccountListKeysResult", keys, namespace="storage")
        assert {k["keyName"] for k in keys["keys"]} == {"key1", "key2"}
        rotated = await client.post(
            path + "/regenerateKey", params=ARGS, headers=headers, json={"keyName": "key1"}
        )
        assert rotated.status_code == 200, rotated.text
        before = {k["keyName"]: k["value"] for k in keys["keys"]}
        after = {k["keyName"]: k["value"] for k in rotated.json()["keys"]}
        assert before["key1"] != after["key1"] and before["key2"] == after["key2"]
        from mockazure.identity.keys import read_wrapping_key
        from mockazure.storage.accounts import key_matches

        async with database.sessions() as session:
            session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
            assert not await key_matches(session, tenant, "evidenceaccount", before["key1"])
            assert await key_matches(session, tenant, "evidenceaccount", before["key2"])
            row = await session.get(ArmResource, (tenant, path.lower()))
            assert all(
                key not in json.dumps(row.payload) for key in (*before.values(), *after.values())
            )
        patch = await client.patch(
            path,
            params=ARGS,
            headers=headers,
            json={"tags": {"purpose": "test"}, "properties": {"allowSharedKeyAccess": False}},
        )
        assert patch.status_code == 200, patch.text
        got = (await client.get(path, params=ARGS, headers=headers)).json()
        assert got["tags"] == {"purpose": "test"}
        assert got["properties"]["allowSharedKeyAccess"] is False
        assert all(key not in json.dumps(got) for key in after.values())
        for collection in (
            f"/subscriptions/{sub}/providers/Microsoft.Storage/storageAccounts",
            group + "/providers/Microsoft.Storage/storageAccounts",
        ):
            listing = await client.get(collection, params=ARGS, headers=headers)
            assert listing.status_code == 200, listing.text
            assert created["id"] in {x["id"] for x in listing.json()["value"]}
        inventory = await client.get(
            f"/subscriptions/{sub}/resources", params={"api-version": "2021-04-01"}, headers=headers
        )
        assert created["id"] in {x["id"] for x in inventory.json()["value"]}
        name_path = f"/subscriptions/{sub}/providers/Microsoft.Storage/checkNameAvailability"
        check = await client.post(
            name_path,
            params=ARGS,
            headers=headers,
            json={"name": "evidenceaccount", "type": "Microsoft.Storage/storageAccounts"},
        )
        assert check.status_code == 200 and check.json()["nameAvailable"] is False
        deleted = await client.delete(path, params=ARGS, headers=headers)
        assert deleted.status_code in (200, 204), deleted.text
        assert (await client.get(path, params=ARGS, headers=headers)).status_code == 404
        async with database.sessions() as session:
            from mockazure.storage.models import StorageAccount

            assert (
                await session.scalar(
                    select(StorageAccount).where(StorageAccount.tenant_id == tenant)
                )
                is None
            )


async def test_account_endpoint_isolation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await setup(client, database, settings, tenant, app)
        endpoints = []
        for name in ("localone", "localtwo"):
            path = group + "/providers/Microsoft.Storage/storageAccounts/" + name
            response = await client.put(path, params=ARGS, headers=headers, json=CREATE)
            assert response.status_code == 200, response.text
            values = response.json()["properties"]["primaryEndpoints"]
            assert set(values) == {"blob", "file", "queue", "table"}
            assert len({urlsplit(url).netloc for url in values.values()}) == 4
            for service, url in values.items():
                assert urlsplit(url).hostname == "localhost"
                assert urlsplit(url).path == "/" + name + "/"
                from mockazure.storage.endpoints import service_endpoint

                assert url == service_endpoint(name, service, settings.endpoints.model_dump())
            endpoints.append(values)
        assert set(endpoints[0].values()).isdisjoint(endpoints[1].values())


async def test_network_context(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await setup(client, database, settings, tenant, app)
        path = group + "/providers/Microsoft.Storage/storageAccounts/networkaccount"
        response = await client.put(
            path,
            params=ARGS,
            headers=headers,
            json=CREATE
            | {
                "properties": {
                    "networkAcls": {
                        "defaultAction": "Deny",
                        "bypass": "AzureServices",
                        "ipRules": [{"value": "192.0.2.0/24", "action": "Allow"}],
                    }
                }
            },
        )
        assert response.status_code == 200, response.text
        from mockazure.storage.accounts import network_allowed

        config = response.json()["properties"]
        assert network_allowed(config, "192.0.2.17", {}) is True
        assert (
            network_allowed(
                config,
                "198.51.100.5",
                {
                    "x-forwarded-for": "192.0.2.17",
                    "x-ms-azure-service": "true",
                    "x-ms-private-endpoint": "true",
                },
            )
            is False
        )
        assert (
            network_allowed(config | {"publicNetworkAccess": "Disabled"}, "192.0.2.17", {}) is False
        )
        assert network_allowed(config, None, {}) is False


async def test_account_validation_permissions_and_service_properties(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await context(
            client, database, settings, tenant, app
        )
        grant = await assign(client, base, principal, f"/subscriptions/{sub}", "Reader")
        path = group + "/providers/Microsoft.Storage/storageAccounts/strictaccount"
        assert (
            await client.put(path, params=ARGS, headers=headers, json=CREATE)
        ).status_code == 403
        await client.delete(base + "/role-assignments/" + grant["name"])
        await assign(client, base, principal, f"/subscriptions/{sub}", "Contributor")
        assert (
            await client.put(path, params=ARGS, headers=headers, json=CREATE)
        ).status_code == 409
        await client.post(
            f"/subscriptions/{sub}/providers/Microsoft.Storage/register",
            params={"api-version": "2021-04-01"},
            headers=headers,
        )
        for payload in (
            CREATE | {"kind": "Imaginary"},
            CREATE | {"properties": {"isNfsV3Enabled": True}},
            CREATE | {"properties": {"minimumTlsVersion": "TLS1_3"}},
            CREATE | {"properties": {"provisioningState": "Succeeded"}},
        ):
            assert (
                await client.put(path, params=ARGS, headers=headers, json=payload)
            ).status_code == 400
        response = await client.put(path, params=ARGS, headers=headers, json=CREATE)
        assert response.status_code == 200, response.text
        assert (
            await client.put(path, params=ARGS, headers=headers, json=CREATE)
        ).status_code == 200
        assert (
            await client.post(
                path + "/regenerateKey", params=ARGS, headers=headers, json={"keyName": "wrong"}
            )
        ).status_code == 400
        for service in ("blob", "file", "queue", "table"):
            service_path = path + "/" + service + "Services/default"
            properties = {"properties": {"cors": {"corsRules": []}}}
            changed = await client.put(service_path, params=ARGS, headers=headers, json=properties)
            assert changed.status_code == 200, changed.text
            got = await client.get(service_path, params=ARGS, headers=headers)
            assert got.status_code == 200 and got.json()["properties"]["cors"] == {"corsRules": []}
        forbidden = await client.put(
            path + "/blobServices/default",
            params=ARGS,
            headers=headers,
            json={"properties": {"invented": True}},
        )
        assert forbidden.status_code == 400


async def test_original_msal_data_audiences(database, settings):
    import asyncio
    import socket
    from datetime import UTC, datetime

    import msal
    import requests
    import uvicorn

    from mockazure.admin.app import create_app
    from mockazure.identity.tokens import validate_access_token
    from mockazure.oauth.errors import OAuthError
    from mockazure.tls import ensure_tls
    from tests.integration.identity_support import identity_client

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    local = settings.model_copy(
        update={"admin_base_url": f"https://localhost:{port}", "listen_port": port}
    )
    tls = ensure_tls(local.data_dir, local.allowed_hosts)
    async with identity_client(database, local) as (client, tenant, app, secret, *_):
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(local, database),
                host="127.0.0.1",
                port=port,
                ssl_certfile=str(tls.server_cert),
                ssl_keyfile=str(tls.server_key),
                proxy_headers=False,
                access_log=False,
                log_level="error",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(200):
                if server.started:
                    break
                if task.done():
                    await task
                await asyncio.sleep(0.01)
            assert server.started

            def acquire():
                with requests.Session() as transport:
                    transport.trust_env = False
                    transport.verify = str(tls.ca_cert)
                    sdk = msal.ConfidentialClientApplication(
                        app,
                        client_credential=secret["secret"],
                        authority=f"https://localhost:{port}/{tenant}",
                        validate_authority=False,
                        instance_discovery=False,
                        http_client=transport,
                    )
                    results = {}
                    for service, audience in (
                        ("storage", "https://storage.azure.com/"),
                        ("keyvault", "https://vault.azure.net"),
                    ):
                        response = sdk.acquire_token_for_client(
                            scopes=[audience.rstrip("/") + "/.default"]
                        )
                        assert "access_token" in response, response.get("error_description")
                        results[service] = response["access_token"]
                    return results

            tokens = await asyncio.to_thread(acquire)
            async with database.sessions() as session:
                for service, token in tokens.items():
                    principal = await validate_access_token(
                        token, service, datetime.now(UTC), session, endpoints=local.endpoints
                    )
                    assert str(principal.tenant_id) == tenant
                    with pytest.raises(OAuthError):
                        await validate_access_token(
                            token, "arm", datetime.now(UTC), session, endpoints=local.endpoints
                        )
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)
            listener.close()


async def test_account_transaction_failure_and_group_delete(database, settings):
    from unittest.mock import patch

    from mockazure.storage.models import StorageAccount

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await setup(client, database, settings, tenant, app)
        path = group + "/providers/Microsoft.Storage/storageAccounts/rollbackaccount"

        async def fail(*args, **kwargs):
            raise ValueError("injected evidence failure")

        with patch("mockazure.storage.accounts.append_arm_fact", fail):
            failed = await client.put(path, params=ARGS, headers=headers, json=CREATE)
        assert failed.status_code == 400
        async with database.sessions() as session:
            assert await session.get(ArmResource, (tenant, path.lower())) is None
            assert (
                await session.scalar(
                    select(StorageAccount).where(StorageAccount.tenant_id == tenant)
                )
                is None
            )
        assert (
            await client.put(path, params=ARGS, headers=headers, json=CREATE)
        ).status_code == 200
        async with database.sessions.begin() as session:
            row = await session.get(ArmResource, (tenant, group.lower()))
            await session.delete(row)
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(StorageAccount).where(StorageAccount.tenant_id == tenant)
                )
                is None
            )


async def test_inventory_import_cannot_overwrite_operational_account(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await setup(client, database, settings, tenant, app)
        path = group + "/providers/Microsoft.Storage/storageAccounts/ownedaccount"
        assert (
            await client.put(path, params=ARGS, headers=headers, json=CREATE)
        ).status_code == 200
        payload = {
            "id": path,
            "name": "ownedaccount",
            "type": "Microsoft.Storage/storageAccounts",
            "location": "eastus",
            "properties": {"networkAcls": {"defaultAction": "Allow"}},
        }
        overwritten = await client.post(base + "/resources", json={"resources": [payload]})
        assert overwritten.status_code in (400, 409, 422), overwritten.text
        got = await client.get(path, params=ARGS, headers=headers)
        assert got.status_code == 200 and got.json()["kind"] == "StorageV2"
        other = group + "/providers/Microsoft.Storage/storageAccounts/importedaccount"
        assert (
            await client.post(
                base + "/resources",
                json={"resources": [payload | {"id": other, "name": "importedaccount"}]},
            )
        ).status_code == 200
        listed = await client.get(
            f"/subscriptions/{sub}/providers/Microsoft.Storage/storageAccounts",
            params=ARGS,
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert {x["id"] for x in listed.json()["value"]} == {path}
        assert (await client.get(other, params=ARGS, headers=headers)).status_code == 409


async def test_keys_denied_and_rotation_failure_is_atomic(database, settings):
    from unittest.mock import patch

    from mockazure.identity.models import ServicePrincipal
    from tests.integration.graph_support import registered_app
    from tests.integration.test_arm_inventory import access

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await setup(client, database, settings, tenant, app)
        path = group + "/providers/Microsoft.Storage/storageAccounts/protectedaccount"
        assert (
            await client.put(path, params=ARGS, headers=headers, json=CREATE)
        ).status_code == 200
        before = (await client.post(path + "/listKeys", params=ARGS, headers=headers)).json()
        reader = await registered_app(client, tenant)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == reader
                )
            )
        reader_headers = await access(database, settings, tenant, reader)
        # No scoped grant must fail even for an empty collection.
        assert (
            await client.get(
                f"/subscriptions/{sub}/providers/Microsoft.Storage/storageAccounts",
                params=ARGS,
                headers=reader_headers,
            )
        ).status_code == 403
        await assign(client, base, sp, f"/subscriptions/{sub}", "Reader")
        assert (await client.get(path, params=ARGS, headers=reader_headers)).status_code == 200
        for action, payload in (("listKeys", {}), ("regenerateKey", {"keyName": "key1"})):
            assert (
                await client.post(
                    path + "/" + action, params=ARGS, headers=reader_headers, json=payload
                )
            ).status_code == 403

        async def fail(*args, **kwargs):
            raise ValueError("injected event failure")

        with patch("mockazure.storage.accounts.append_service_event", fail):
            response = await client.post(
                path + "/regenerateKey", params=ARGS, headers=headers, json={"keyName": "key1"}
            )
        assert response.status_code == 400
        assert (
            await client.post(path + "/listKeys", params=ARGS, headers=headers)
        ).json() == before


async def test_storage_fixture_lifecycle_excludes_keys_and_rejects_foreign_state(
    database, settings
):
    from datetime import UTC, datetime
    from uuid import uuid4

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await setup(client, database, settings, tenant, app)
        path = group + "/providers/Microsoft.Storage/storageAccounts/lifecycleaccount"
        assert (
            await client.put(path, params=ARGS, headers=headers, json=CREATE)
        ).status_code == 200
        old_keys = (await client.post(path + "/listKeys", params=ARGS, headers=headers)).json()
        local = f"/_mock/tenants/{tenant}/cloud/storageState"
        exported = await client.get(local)
        assert exported.status_code == 200, exported.text
        state = exported.json()
        assert len(state["accounts"]) == 1
        assert all(k["value"] not in exported.text for k in old_keys["keys"])
        assert "key1" not in exported.text and "key2" not in exported.text
        broken = json.loads(exported.text)
        broken["accounts"][0]["id"] = path.replace(str(sub), str(uuid4()))
        assert (await client.put(local, json=broken)).status_code == 422
        assert (
            await client.post(path + "/listKeys", params=ARGS, headers=headers)
        ).json() == old_keys
        restored = await client.put(local, json=state)
        assert restored.status_code == 200, restored.text
        new_keys = (await client.post(path + "/listKeys", params=ARGS, headers=headers)).json()
        assert not {k["value"] for k in old_keys["keys"]} & {k["value"] for k in new_keys["keys"]}
        assert (await client.get(path, params=ARGS, headers=headers)).status_code == 200
        from mockazure.storage.lifecycle import prune_storage_accounts

        async with database.sessions.begin() as session:
            assert await prune_storage_accounts(session, tenant, datetime.now(UTC), 5) == 0
        assert (await client.put(local, json={"accounts": []})).status_code == 200
        assert (await client.get(path, params=ARGS, headers=headers)).status_code == 404
        assert (await client.get(local)).json() == {"accounts": []}


def test_storage_upgrade_from_preceding_head(settings):
    from alembic import command
    from sqlalchemy import create_engine, text

    from mockazure.db.migrations import configuration, migrate

    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "057")
            assert connection.scalar(text("SELECT to_regclass('storage_accounts')")) is None
        migrate(settings.database_url.get_secret_value())
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "065"
            assert (
                connection.scalar(text("SELECT to_regclass('storage_accounts')"))
                == "storage_accounts"
            )
    finally:
        engine.dispose()
