"""Provider wire contracts and current control/data-plane separation."""

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select

from mockazure.identity.models import ServicePrincipal
from mockazure.providers.arm.authorization import require_action
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.scopes import parse_scope
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import actor, assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm

READ = "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"
WRITE = "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write"


async def context(client, database, settings, tenant, app):
    sub, base, group, _ = await seed_arm(client, tenant)
    async with database.sessions() as session:
        principal = await session.scalar(
            select(ServicePrincipal.id).where(
                ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
            )
        )
    return sub, base, group, principal, await access(database, settings, tenant, app)


async def test_provider_discovery_and_version(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await context(
            client, database, settings, tenant, app
        )
        await assign(client, base, principal, f"/subscriptions/{sub}", "Contributor")
        evidence = f"/_mock/tenants/{tenant}/evidence"
        assert (
            await client.patch(
                evidence + "/settings",
                json={"delay_seconds": 0},
            )
        ).status_code == 200
        path = f"/subscriptions/{sub}/providers"
        args = {"api-version": "2021-04-01"}
        response = await client.get(path, params=args, headers=headers)
        assert response.status_code == 200, response.text
        validate_arm_payload("ProviderListResult", response.json(), namespace="resources")
        providers = {row["namespace"]: row for row in response.json()["value"]}
        assert set(providers) == {
            "Microsoft.Storage",
            "Microsoft.KeyVault",
            "Microsoft.Compute",
            "Microsoft.Network",
            "Microsoft.OperationalInsights",
            "Microsoft.Insights",
        }
        storage = providers["Microsoft.Storage"]
        assert storage["registrationState"] == "NotRegistered"
        assert any(
            row["resourceType"] == "storageAccounts" and "2025-06-01" in row["apiVersions"]
            for row in storage["resourceTypes"]
        )
        for action, state in [("register", "Registered"), ("unregister", "NotRegistered")]:
            changed = await client.post(
                path + "/Microsoft.Storage/" + action, params=args, headers=headers
            )
            assert changed.status_code == 200, changed.text
            validate_arm_payload("Provider", changed.json(), namespace="resources")
            assert changed.json()["registrationState"] == state
            read = await client.get(path + "/microsoft.storage", params=args, headers=headers)
            assert read.json()["registrationState"] == state
        assert (
            await client.get(path + "/Microsoft.Unknown", params=args, headers=headers)
        ).status_code == 404
        assert (
            await client.get(path, params={"api-version": "2099-01-01"}, headers=headers)
        ).status_code == 400
        assert (
            await client.get(path, params=args | {"$expand": "invented"}, headers=headers)
        ).status_code == 400
        denied = await client.post(path + "/Microsoft.Storage/register", params=args, headers={})
        assert denied.status_code == 401
        from mockazure.providers.arm.provider_registry import resolve_operation

        assert resolve_operation("GET", path, "2021-04-01")["operationId"] == "Providers_List"
        with pytest.raises(ArmError):
            resolve_operation("PUT", group + "/providers/Microsoft.Unknown/things/x", "2021-04-01")
        drained = await client.post(evidence + "/drain", json={"max_jobs": 100})
        assert drained.status_code == 200 and drained.json()["failed"] == 0
        events = await client.get(
            f"/subscriptions/{sub}/providers/Microsoft.Insights/eventtypes/management/values",
            params={
                "api-version": "2015-04-01",
                "$filter": "eventTimestamp ge '2026-01-01T00:00:00Z'",
            },
            headers=headers,
        )
        assert events.status_code == 200, events.text
        registration = [
            r
            for r in events.json()["value"]
            if r["resourceId"].lower() == (path + "/Microsoft.Storage").lower()
        ]
        assert {r["operationName"]["value"] for r in registration} == {
            "Microsoft.Storage/register/action",
            "Microsoft.Storage/unregister/action",
        }


async def test_actions_are_not_data_actions(database, settings):
    from mockazure.providers.arm.data_authorization import require_data_action

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await context(
            client, database, settings, tenant, app
        )
        await assign(client, base, principal, group, "Contributor")
        account = group + "/providers/Microsoft.Storage/storageAccounts/localaccount"
        seed = await client.post(
            base + "/resources",
            json={
                "resources": [
                    {
                        "id": account,
                        "name": "localaccount",
                        "type": "Microsoft.Storage/storageAccounts",
                        "location": "eastus",
                        "properties": {},
                    }
                ]
            },
        )
        assert seed.status_code == 200, seed.text
        storage = replace(actor(tenant, app), audience="https://storage.azure.com/")
        async with database.sessions() as session:
            await require_action(
                session,
                actor(tenant, app),
                parse_scope(account),
                "Microsoft.Storage/storageAccounts/write",
            )
            with pytest.raises(ArmError):
                await require_data_action(session, storage, account, READ)
        grant = await assign(client, base, principal, group, "Storage Blob Data Reader")
        async with database.sessions() as session:
            await require_data_action(session, storage, account.upper(), READ)
            for wrong in [actor(tenant, app), replace(storage, audience="https://vault.azure.net")]:
                with pytest.raises(ArmError):
                    await require_data_action(session, wrong, account, READ)
            with pytest.raises(ArmError):
                await require_data_action(session, storage, account, WRITE)
            with pytest.raises(ArmError):
                await require_data_action(
                    session, storage, account.replace(str(sub), str(uuid4())), READ
                )
        assert (await client.delete(base + "/role-assignments/" + grant["name"])).status_code == 200
        async with database.sessions() as session:
            with pytest.raises(ArmError):
                await require_data_action(session, storage, account, READ)


async def test_scoped_role_union(database, settings, monkeypatch):
    import copy

    import mockazure.providers.arm.authorization as auth
    from mockazure.providers.arm.data_authorization import require_data_action

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await context(
            client, database, settings, tenant, app
        )
        await assign(client, base, principal, group, "Storage Blob Data Owner")
        roles = copy.deepcopy(auth.builtin_roles())
        roles["Storage Blob Data Owner"]["notDataActions"] = [READ]
        monkeypatch.setattr(auth, "builtin_roles", lambda: roles)
        storage = replace(actor(tenant, app), audience="https://storage.azure.com/")
        async with database.sessions() as session:
            with pytest.raises(ArmError):
                await require_data_action(session, storage, group, READ)
            await require_data_action(session, storage, group, WRITE)
        await assign(client, base, principal, group, "Storage Blob Data Reader")
        async with database.sessions() as session:
            await require_data_action(session, storage, group, READ)
        await client.patch(
            f"/_mock/tenants/{tenant}/identity/applications/{app}", json={"enabled": False}
        )
        async with database.sessions() as session:
            with pytest.raises(ArmError):
                await require_data_action(session, storage, group, READ)


async def test_provider_state_and_event_lifecycle(database, settings):
    from datetime import UTC, datetime

    from mockazure.cloud.events import append_service_event
    from mockazure.cloud.models import CloudEvent
    from mockazure.cloud.types import ServiceEvent

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await context(
            client, database, settings, tenant, app
        )
        await assign(client, base, principal, f"/subscriptions/{sub}", "Contributor")
        result = await client.post(
            f"/subscriptions/{sub}/providers/Microsoft.Storage/register",
            params={"api-version": "2021-04-01"},
            headers=headers,
        )
        assert result.status_code == 200, result.text
        event = ServiceEvent(
            uuid4(),
            tenant,
            group,
            "storage",
            "StorageWrite",
            "PutBlob",
            datetime.now(UTC),
            {"path": "container/file.txt", "statusCode": 201},
        )
        async with database.sessions.begin() as session:
            assert await append_service_event(session, event) == event.id
            assert await append_service_event(session, event) == event.id
            with pytest.raises(ValueError):
                await append_service_event(session, replace(event, payload={"statusCode": 500}))
        async with database.sessions() as session:
            assert (
                len(
                    list(
                        await session.scalars(
                            select(CloudEvent).where(
                                CloudEvent.tenant_id == tenant, CloudEvent.id == event.id
                            )
                        )
                    )
                )
                == 1
            )
        # A historical fact must survive deletion of its source resource group.
        from mockazure.providers.arm.models import ArmResource

        async with database.sessions.begin() as session:
            await session.delete(await session.get(ArmResource, (tenant, group.lower())))
        path = f"/_mock/tenants/{tenant}/cloud/providerState"
        exported = await client.get(path)
        assert exported.status_code == 200, exported.text
        assert exported.json()["registrations"][0]["namespace"] == "Microsoft.Storage"
        assert any(row["id"] == str(event.id) for row in exported.json()["events"])
        bad = exported.json()
        bad["registrations"][0]["subscription_id"] = str(uuid4())
        assert (await client.put(path, json=bad)).status_code == 422
        assert (await client.get(path)).json() == exported.json()
        assert (await client.put(path, json={"registrations": [], "events": []})).status_code == 200
        restored = await client.put(path, json=exported.json())
        assert restored.status_code == 200, restored.text
        assert restored.json() == exported.json()
        async with database.sessions() as session:
            rows = list(
                await session.scalars(select(CloudEvent).where(CloudEvent.tenant_id == tenant))
            )
            assert rows and all(not row.publish_eligible for row in rows)


async def test_provider_upgrade_restart_and_lossy_downgrade(database, settings):
    import httpx
    from alembic import command as alembic_command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.admin.app import create_app
    from mockazure.db.migrations import configuration

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await context(
            client, database, settings, tenant, app
        )
        engine = create_engine(settings.database_url.get_secret_value())
        try:
            config = configuration()
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                alembic_command.downgrade(config, "056")
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM arm_resources WHERE tenant_id=:tenant"),
                        {"tenant": tenant},
                    )
                    == 2
                )
                alembic_command.upgrade(config, "head")
            await assign(client, base, principal, f"/subscriptions/{sub}", "Contributor")
            path = f"/subscriptions/{sub}/providers/Microsoft.Storage"
            assert (
                await client.post(
                    path + "/register", params={"api-version": "2021-04-01"}, headers=headers
                )
            ).status_code == 200
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(settings, database)),
                base_url="https://localhost:8443",
            ) as restarted:
                actual = await restarted.get(
                    path, params={"api-version": "2021-04-01"}, headers=headers
                )
                assert (
                    actual.status_code == 200 and actual.json()["registrationState"] == "Registered"
                ), actual.text
            with pytest.raises(DBAPIError, match="Cannot discard cloud provider or event state"):
                with engine.begin() as connection:
                    config.attributes["connection"] = connection
                    alembic_command.downgrade(config, "056")
        finally:
            engine.dispose()


async def test_denied_registration_preserves_state_and_event_payload_secrecy(database, settings):
    from datetime import UTC, datetime

    from mockazure.cloud.events import append_service_event
    from mockazure.cloud.types import ServiceEvent

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await context(
            client, database, settings, tenant, app
        )
        await assign(client, base, principal, f"/subscriptions/{sub}", "Reader")
        fixture = f"/_mock/tenants/{tenant}/cloud/providerState"
        before = (await client.get(fixture)).json()
        denied = await client.post(
            f"/subscriptions/{sub}/providers/Microsoft.Storage/register",
            params={"api-version": "2021-04-01"},
            headers=headers,
        )
        assert denied.status_code == 403, denied.text
        assert (await client.get(fixture)).json() == before
        await assign(client, base, principal, f"/subscriptions/{sub}", "Contributor")
        conflict = await client.post(
            f"/subscriptions/{sub}/providers/Microsoft.Compute/unregister",
            params={"api-version": "2021-04-01"},
            headers=headers,
        )
        assert conflict.status_code == 409, conflict.text
        assert (await client.get(fixture)).json() == before
        async with database.sessions.begin() as session:
            event = ServiceEvent(
                uuid4(),
                tenant,
                group,
                "keyvault",
                "AuditEvent",
                "SecretSet",
                datetime.now(UTC),
                {"secret": "must-never-enter-evidence"},
            )
            with pytest.raises(ValueError):
                await append_service_event(session, event)
        assert (await client.get(fixture)).json() == before


async def test_registration_uses_existing_arm_lock_order(database, settings, monkeypatch):
    import asyncio
    from uuid import UUID

    from sqlalchemy import text

    from mockazure.evidence.repository import lock_state
    from mockazure.providers.arm import facts, mutations
    from mockazure.providers.arm.inventory import lock_subscription

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, principal, headers = await context(
            client, database, settings, tenant, app
        )
        await assign(client, base, principal, f"/subscriptions/{sub}", "Contributor")
        waiting = asyncio.Event()

        async def observed_lock(session, identifier):
            waiting.set()
            return await lock_state(session, identifier)

        monkeypatch.setattr(facts, "lock_state", observed_lock)
        monkeypatch.setattr(mutations, "lock_state", observed_lock)
        pending = None
        try:
            async with database.sessions.begin() as blocker:
                await lock_state(blocker, tenant)
                pending = asyncio.create_task(
                    client.post(
                        f"/subscriptions/{sub}/providers/Microsoft.Storage/register",
                        params={"api-version": "2021-04-01"},
                        headers=headers,
                    )
                )
                await asyncio.wait_for(waiting.wait(), 5)
                # Evidence is acquired before subscription by existing ARM writes.
                # A waiting registration must not hold the subscription lock.
                async with database.sessions.begin() as probe:
                    await probe.execute(text("SET LOCAL lock_timeout='500ms'"))
                    await lock_subscription(probe, tenant, UUID(sub))
        finally:
            if pending is not None:
                result = await asyncio.wait_for(pending, 5)
                assert result.status_code == 200, result.text
