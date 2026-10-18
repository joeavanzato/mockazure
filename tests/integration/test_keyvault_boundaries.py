"""Secret privacy, redacted lifecycle and request authorization boundaries."""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mockazure.cloud.lifecycle import export_cloud, import_cloud, prune_cloud, reset_cloud
from mockazure.cloud.models import CloudEvent
from mockazure.identity.keys import read_wrapping_key
from mockazure.keyvault.secret_models import SecretVersion
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_cloud_resource_inventory import VERSIONS
from tests.integration.test_keyvault_secrets import ARGS, provision_vault


async def test_secret_default_export_and_ciphertext_binding(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        first = await client.put(
            base + "secrets/alpha",
            params=ARGS,
            headers=head,
            json={"value": "never-export-this-value"},
        )
        second = await client.put(
            base + "secrets/beta",
            params=ARGS,
            headers=head,
            json={"value": "different-secret-value"},
        )
        assert first.status_code == second.status_code == 200
        async with database.sessions.begin() as session:
            versions = list(
                await session.scalars(
                    select(SecretVersion)
                    .where(SecretVersion.tenant_id == tenant)
                    .order_by(SecretVersion.created_at)
                )
            )
            assert all("never-export-this-value" not in row.encrypted_value for row in versions)
            saved = versions[1].encrypted_value
            versions[1].encrypted_value = versions[0].encrypted_value
        refused = await client.get(second.json()["id"], params=ARGS, headers=head)
        assert refused.status_code == 503 and "never-export" not in refused.text
        async with database.sessions.begin() as session:
            versions = list(
                await session.scalars(
                    select(SecretVersion)
                    .where(SecretVersion.tenant_id == tenant)
                    .order_by(SecretVersion.created_at)
                )
            )
            versions[1].encrypted_value = saved
            state = await export_cloud(session, tenant)
            assert len(state["keyvault"]["secrets"]) == 2
            serialized = json.dumps(state)
            assert "never-export-this-value" not in serialized and saved not in serialized
            facts = list(
                await session.scalars(select(CloudEvent).where(CloudEvent.tenant_id == tenant))
            )
            assert all("never-export-this-value" not in json.dumps(f.payload) for f in facts)
            bad = copy.deepcopy(state)
            bad["keyvault"]["secrets"][0]["versions"][0]["value"] = "injected"
            with pytest.raises(Exception) as caught:
                await import_cloud(session, tenant, bad)
            assert getattr(caught.value, "status", 422) == 422
        assert (await client.get(first.json()["id"], params=ARGS, headers=head)).status_code == 200
        async with database.sessions.begin() as session:
            session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
            await import_cloud(session, tenant, state)
        response = await client.get(first.json()["id"], params=ARGS, headers=head)
        assert (
            response.status_code == 409
            and response.json()["error"]["code"] == "SecretValueUnavailable"
        )
        listing = await client.get(base + "secrets", params=ARGS, headers=head)
        assert len(listing.json()["value"]) == 2
        async with database.sessions.begin() as session:
            state = await export_cloud(session, tenant)
            await reset_cloud(session, tenant, state["generation"])
        assert (await client.get(first.json()["id"], params=ARGS, headers=head)).status_code == 404


async def test_secret_validation_metadata_grants_and_challenge(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, policy = await provision_vault(
            client, database, settings, tenant, app, policies=True
        )
        url = base + "secrets/validate"
        for body in (
            {"value": 1},
            {"value": "ok", "attributes": {"created": 1}},
            {"value": "ok", "managed": True},
            {"value": "x" * 25601},
            {"value": "ok", "attributes": {"enabled": "yes"}},
        ):
            response = await client.put(url, params=ARGS, headers=head, json=body)
            assert response.status_code == 400, response.text
        response = await client.get(url, params=ARGS, headers={"Authorization": ""})
        assert response.status_code == 401 and str(tenant) in response.headers["WWW-Authenticate"]
        assert 'resource="https://vault.azure.net"' in response.headers["WWW-Authenticate"]
        assert (
            await client.put(url, params=ARGS, headers=head, json={"value": "limited"})
        ).status_code == 200
        policy["permissions"]["secrets"] = ["list", "set"]
        assert (
            await client.patch(
                path,
                params={"api-version": VERSIONS["Microsoft.KeyVault"]},
                headers=arm,
                json={"properties": {"accessPolicies": [policy]}},
            )
        ).status_code == 200
        assert (await client.get(url, params=ARGS, headers=head)).status_code == 403
        result = await client.patch(url, params=ARGS, headers=head, json={"tags": {"a": "b"}})
        assert result.status_code == 200 and "value" not in result.json()
        assert (await client.get(base + "secrets", params=ARGS, headers=head)).status_code == 200
        denied = await client.patch(
            url, params=ARGS, headers=head, json={"value": "illegal replacement"}
        )
        assert denied.status_code == 400
        disabled = await client.patch(
            path,
            params={"api-version": VERSIONS["Microsoft.KeyVault"]},
            headers=arm,
            json={"properties": {"publicNetworkAccess": "Disabled"}},
        )
        assert disabled.status_code == 200
        assert (
            await client.get(
                base + "secrets", params=ARGS, headers=head | {"X-Forwarded-For": "127.0.0.1"}
            )
        ).status_code == 403


async def test_secret_paging_restore_validation_and_cleanup(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        for name in ("one", "two", "three"):
            assert (
                await client.put(
                    base + "secrets/" + name,
                    params=ARGS,
                    headers=head,
                    json={"value": "payload-" + name},
                )
            ).status_code == 200
        page = await client.get(base + "secrets", params=ARGS | {"maxresults": "1"}, headers=head)
        continuation = page.json()["nextLink"]
        assert (
            await client.get(continuation.replace("maxresults=1", "maxresults=2"), headers=head)
        ).status_code == 400
        async with database.sessions.begin() as session:
            state = await export_cloud(session, tenant)
            invalid = copy.deepcopy(state)
            invalid["keyvault"]["secrets"][0]["vault_id"] = (
                path.rsplit("/", 1)[0] + "/missing-vault"
            )
            with pytest.raises(Exception) as caught:
                await import_cloud(session, tenant, invalid)
            assert getattr(caught.value, "status", 422) == 422
        assert (await client.get(base + "secrets/one", params=ARGS, headers=head)).json()[
            "value"
        ] == "payload-one"
        async with database.sessions.begin() as session:
            await prune_cloud(session, tenant, datetime.now(UTC) + timedelta(hours=2), 100)
        assert (await client.get(continuation, headers=head)).status_code == 400
        page = await client.get(base + "secrets", params=ARGS | {"maxresults": "1"}, headers=head)
        continuation = page.json()["nextLink"]
        async with database.sessions.begin() as session:
            session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
            await import_cloud(session, tenant, state)
        assert (await client.get(continuation, headers=head)).status_code == 400


async def test_secret_migration_refuses_lossy_downgrade(database, settings):
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration

    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        assert (
            await client.put(
                base + "secrets/preserved", params=ARGS, headers=head, json={"value": "preserve"}
            )
        ).status_code == 200
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with pytest.raises(DBAPIError, match="Export and reset secret state"):
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "065")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "066"
            assert connection.scalar(text("SELECT name FROM keyvault_secrets")) == "preserved"
    finally:
        engine.dispose()
