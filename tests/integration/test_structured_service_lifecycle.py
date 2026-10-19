"""Structured data recovery, protected secrets and explicit encrypted backups."""

import copy
import json
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from mockazure.cloud.lifecycle import export_cloud, import_cloud, prune_cloud
from mockazure.identity.keys import read_wrapping_key
from mockazure.keyvault.secret_models import SecretVersion
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_cloud_resource_inventory import VERSIONS, complete_delete
from tests.integration.test_keyvault_secrets import ARGS, provision_vault
from tests.integration.test_queue_workflows import message, queue_call
from tests.integration.test_storage_authorization import provision
from tests.integration.test_table_workflows import table_call


async def test_secret_recovery_and_protection(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        url = base + "secrets/recoverable"
        first = (await client.put(url, params=ARGS, headers=head, json={"value": "older"})).json()
        second = (await client.put(url, params=ARGS, headers=head, json={"value": "newer"})).json()
        deleted = await client.delete(url, params=ARGS, headers=head)
        assert deleted.status_code == 200, deleted.text
        assert (
            "value" not in deleted.json()
            and deleted.json()["scheduledPurgeDate"] > deleted.json()["deletedDate"]
        )
        assert (await client.get(url, params=ARGS, headers=head)).status_code == 404
        tomb = base + "deletedsecrets/recoverable"
        assert (await client.get(tomb, params=ARGS, headers=head)).status_code == 200
        assert (
            len(
                (await client.get(base + "deletedsecrets", params=ARGS, headers=head)).json()[
                    "value"
                ]
            )
            == 1
        )
        assert (
            await client.put(url, params=ARGS, headers=head, json={"value": "forbidden reuse"})
        ).status_code == 409
        recovered = await client.post(tomb + "/recover", params=ARGS, headers=head)
        assert recovered.status_code == 200
        assert (await client.get(first["id"], params=ARGS, headers=head)).json()["value"] == "older"
        assert (await client.get(url, params=ARGS, headers=head)).json()["id"] == second["id"]
        await client.delete(url, params=ARGS, headers=head)
        assert (await client.delete(tomb, params=ARGS, headers=head)).status_code == 204
        await client.put(url, params=ARGS, headers=head, json={"value": "protected"})
        enabled = await client.patch(
            path,
            params={"api-version": VERSIONS["Microsoft.KeyVault"]},
            headers=arm,
            json={"properties": {"enablePurgeProtection": True}},
        )
        assert enabled.status_code == 200
        await client.delete(url, params=ARGS, headers=head)
        assert (await client.delete(tomb, params=ARGS, headers=head)).status_code == 403
        async with database.sessions.begin() as session:
            await prune_cloud(session, tenant, datetime.now(UTC) + timedelta(days=91), 100)
        assert (await client.get(tomb, params=ARGS, headers=head)).status_code == 404


async def test_structured_restore_and_isolation(database, settings):
    backup_file = settings.data_dir / "explicit-secret-backup.key"
    backup_file.write_bytes(Fernet.generate_key())
    local = settings.model_copy(
        update={
            "keyvault_backup_key_file": backup_file,
            "keyvault_backup_key_ref": "test-restore-key",
        }
    )
    async with graph_client(database, local) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, local, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        url = base + "secrets/backup-me"
        first = (
            await client.put(url, params=ARGS, headers=head, json={"value": "backup-private-one"})
        ).json()
        second = (
            await client.put(url, params=ARGS, headers=head, json={"value": "backup-private-two"})
        ).json()
        backup = await client.post(url + "/backup", params=ARGS, headers=head)
        assert backup.status_code == 200, backup.text
        assert "backup-private" not in backup.text
        _, _, endpoints, key = await provision(client, database, local, tenant, app)
        assert (
            await table_call(
                client, "POST", endpoints["table"] + "Tables", key, body={"TableName": "Recover"}
            )
        ).status_code == 201
        assert (
            await table_call(
                client,
                "POST",
                endpoints["table"] + "Recover",
                key,
                body={"PartitionKey": "p", "RowKey": "r", "Count": 7},
            )
        ).status_code == 201
        assert (
            await queue_call(client, "PUT", endpoints["queue"] + "recover", key)
        ).status_code == 201
        assert (
            await queue_call(
                client,
                "POST",
                endpoints["queue"] + "recover/messages",
                key,
                body=message("queue evidence"),
            )
        ).status_code == 201
        async with database.sessions.begin() as session:
            state = await export_cloud(session, tenant)
            assert "backup-private" not in json.dumps(state)
            session.info["storage_cipher"] = read_wrapping_key(local.data_dir)
            await import_cloud(session, tenant, state)
        assert (await client.get(url, params=ARGS, headers=head)).status_code == 409
        # Backup restore cannot overwrite an active name; explicit deletion/purge precedes it.
        assert (
            await client.post(
                base + "secrets/restore", params=ARGS, headers=head, json=backup.json()
            )
        ).status_code == 409
        await client.delete(url, params=ARGS, headers=head)
        await client.delete(base + "deletedsecrets/backup-me", params=ARGS, headers=head)
        restored = await client.post(
            base + "secrets/restore", params=ARGS, headers=head, json=backup.json()
        )
        assert restored.status_code == 200, restored.text
        assert (await client.get(first["id"], params=ARGS, headers=head)).json()[
            "value"
        ] == "backup-private-one"
        assert (await client.get(second["id"], params=ARGS, headers=head)).json()[
            "value"
        ] == "backup-private-two"
        async with database.sessions.begin() as session:
            state = await export_cloud(session, tenant)
            assert (
                state["storage"]["accounts"][0]["tables"][0]["entities"][0]["properties"]["Count"][
                    "value"
                ]
                == 7
            )
            assert state["storage"]["accounts"][0]["queue_messages"][0]["body"] == "queue evidence"
        corrupt = copy.deepcopy(backup.json())
        corrupt["value"] = corrupt["value"][:-8] + "invalid!"
        assert (
            await client.post(base + "secrets/restore", params=ARGS, headers=head, json=corrupt)
        ).status_code == 400


async def test_vault_delete_preserves_protected_secret_state(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        assert (
            await client.put(
                base + "secrets/preserved",
                params=ARGS,
                headers=head,
                json={"value": "survives-vault-delete"},
            )
        ).status_code == 200
        assert (
            await client.patch(
                path,
                params={"api-version": VERSIONS["Microsoft.KeyVault"]},
                headers=arm,
                json={"properties": {"enablePurgeProtection": True}},
            )
        ).status_code == 200
        deleted = await client.delete(
            path, params={"api-version": VERSIONS["Microsoft.KeyVault"]}, headers=arm
        )
        assert deleted.status_code in {202, 204}, deleted.text
        if deleted.status_code == 202:
            await complete_delete(database, tenant)
        assert (
            await client.get(base + "secrets/preserved", params=ARGS, headers=head)
        ).status_code == 404
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SecretVersion)
                    .where(SecretVersion.tenant_id == tenant)
                )
                == 1
            )


async def test_deleted_vault_recovery_retains_values_but_not_scoped_grants(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        version = (
            await client.put(
                base + "secrets/retained",
                params=ARGS,
                headers=head,
                json={"value": "retained-value"},
            )
        ).json()["id"]
        args = {"api-version": VERSIONS["Microsoft.KeyVault"]}
        config = (await client.get(path, params=args, headers=arm)).json()
        body = {
            "location": config["location"],
            "properties": {
                "tenantId": str(tenant),
                "sku": config["properties"]["sku"],
                "enableRbacAuthorization": True,
            },
        }
        sub = path.split("/resourceGroups", 1)[0]
        tomb = sub + "/providers/Microsoft.KeyVault/locations/eastus/deletedVaults/local-vault"
        assert (await client.delete(path, params=args, headers=arm)).status_code == 204
        deleted = await client.get(tomb, params=args, headers=arm)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["properties"]["vaultId"].lower() == path.lower()
        assert (await client.put(path, params=args, headers=arm, json=body)).status_code == 409
        listed = await client.get(
            sub + "/providers/Microsoft.KeyVault/deletedVaults", params=args, headers=arm
        )
        assert listed.status_code == 200 and len(listed.json()["value"]) == 1
        async with database.sessions.begin() as session:
            state = await export_cloud(session, tenant)
            assert len(state["keyvault"]["deleted_vaults"]) == 1
            assert "retained-value" not in json.dumps(state)
        body["properties"]["createMode"] = "recover"
        recovered = await client.put(path, params=args, headers=arm, json=body)
        assert recovered.status_code in {200, 201}, recovered.text
        assert (await client.get(version, params=ARGS, headers=head)).status_code == 403
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        assert (await client.get(version, params=ARGS, headers=head)).json()[
            "value"
        ] == "retained-value"
        await client.delete(path, params=args, headers=arm)
        assert (await client.post(tomb + "/purge", params=args, headers=arm)).status_code == 200
        assert (await client.get(tomb, params=args, headers=arm)).status_code == 404
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SecretVersion)
                    .where(SecretVersion.tenant_id == tenant)
                )
                == 0
            )


async def test_deleted_vault_protection_and_snapshot_restore(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        await client.put(
            base + "secrets/retained", params=ARGS, headers=head, json={"value": "private"}
        )
        args = {"api-version": VERSIONS["Microsoft.KeyVault"]}
        await client.patch(
            path, params=args, headers=arm, json={"properties": {"enablePurgeProtection": True}}
        )
        await client.delete(path, params=args, headers=arm)
        tomb = (
            path.split("/resourceGroups", 1)[0]
            + "/providers/Microsoft.KeyVault/locations/eastus/deletedVaults/local-vault"
        )
        assert (await client.post(tomb + "/purge", params=args, headers=arm)).status_code == 403
        async with database.sessions.begin() as session:
            state = await export_cloud(session, tenant)
            await import_cloud(session, tenant, state)
        assert (await client.get(tomb, params=args, headers=arm)).status_code == 200
        async with database.sessions.begin() as session:
            await prune_cloud(session, tenant, datetime.now(UTC) + timedelta(days=91), 100)
        assert (await client.get(tomb, params=args, headers=arm)).status_code == 404
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(SecretVersion)
                    .where(SecretVersion.tenant_id == tenant)
                )
                == 0
            )


async def test_backup_cross_vault_restore_authority_and_envelope(database, settings):
    from uuid import uuid4

    from sqlalchemy import delete

    from mockazure.providers.arm.models import RoleAssignment
    from tests.integration.test_cloud_resource_inventory import create

    backup_file = settings.data_dir / "explicit-backup.key"
    backup_file.write_bytes(Fernet.generate_key())
    local = settings.model_copy(
        update={"keyvault_backup_key_file": backup_file, "keyvault_backup_key_ref": "backup-v1"}
    )
    async with graph_client(database, local) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, local, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        first = (
            await client.put(
                base + "secrets/shared", params=ARGS, headers=head, json={"value": "retained-first"}
            )
        ).json()
        second = (
            await client.put(
                base + "secrets/shared",
                params=ARGS,
                headers=head,
                json={"value": "retained-second"},
            )
        ).json()
        backup = await client.post(base + "secrets/shared/backup", params=ARGS, headers=head)
        assert backup.status_code == 200
        second_path = path.rsplit("/", 1)[0] + "/second-vault"
        target = await create(
            client,
            second_path,
            {
                "location": "westus2",
                "properties": {
                    "tenantId": str(tenant),
                    "sku": {"family": "A", "name": "standard"},
                    "enableRbacAuthorization": True,
                },
            },
            arm,
            VERSIONS["Microsoft.KeyVault"],
        )
        target_base = target["properties"]["vaultUri"]
        assert (
            await client.post(
                target_base + "secrets/restore", params=ARGS, headers=head, json=backup.json()
            )
        ).status_code == 403
        await assign(client, admin, principal, second_path, "Key Vault Secrets Officer")
        result = await client.post(
            target_base + "secrets/restore", params=ARGS, headers=head, json=backup.json()
        )
        assert result.status_code == 200, result.text
        assert "value" not in result.json()
        assert result.json()["id"].rsplit("/", 1)[1] == second["id"].rsplit("/", 1)[1]
        old = target_base + "secrets/shared/" + first["id"].rsplit("/", 1)[1]
        assert (await client.get(old, params=ARGS, headers=head)).json()[
            "value"
        ] == "retained-first"
        assert (await client.get(first["id"], params=ARGS, headers=head)).json()[
            "value"
        ] == "retained-first"
        cipher = Fernet(backup_file.read_bytes())
        original = json.loads(cipher.decrypt(backup.json()["value"].encode()))
        for field, expected in (
            ("tenant_id", 403),
            ("subscription_id", 403),
            ("geography", 403),
            ("key_ref", 400),
            ("manifest_version", 400),
        ):
            changed = copy.deepcopy(original)
            changed[field] = 2 if field == "manifest_version" else str(uuid4())
            envelope = {"value": cipher.encrypt(json.dumps(changed).encode()).decode()}
            assert (
                await client.post(
                    target_base + "secrets/restore", params=ARGS, headers=head, json=envelope
                )
            ).status_code == expected
        async with database.sessions.begin() as session:
            await session.execute(
                delete(RoleAssignment).where(
                    RoleAssignment.tenant_id == tenant, RoleAssignment.scope_key == path.lower()
                )
            )
        assert (
            await client.post(base + "secrets/shared/backup", params=ARGS, headers=head)
        ).status_code == 403


async def test_backup_requires_operator_key(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await assign(client, admin, principal, path, "Key Vault Secrets Officer")
        await client.put(
            base + "secrets/no-key", params=ARGS, headers=head, json={"value": "local-only"}
        )
        result = await client.post(base + "secrets/no-key/backup", params=ARGS, headers=head)
        assert result.status_code == 409 and result.json()["error"]["code"] == "BackupKeyRequired"


async def test_fixture_import_cannot_claim_other_tenants_deleted_vault_name(database, settings):
    from uuid import UUID

    import pytest

    from mockazure.admin.errors import AdminError
    from tests.integration.graph_support import registered_app
    from tests.integration.test_cloud_resource_inventory import prepare

    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, _ = await provision_vault(
            client, database, settings, tenant, app
        )
        await client.delete(
            path, params={"api-version": VERSIONS["Microsoft.KeyVault"]}, headers=arm
        )
        other = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "other",
                    "display_name": "Other",
                    "domain": "other.test",
                    "licences": ["E5"],
                },
            )
        ).json()["id"]
        other_app = await registered_app(client, other)
        _, _, group, _, _ = await prepare(client, database, settings, UUID(other), UUID(other_app))
        async with database.sessions.begin() as session:
            state = await export_cloud(session, UUID(other))
        state["resources"]["resources"].append(
            {
                "id": group + "/providers/Microsoft.KeyVault/vaults/local-vault",
                "configuration": {
                    "location": "eastus",
                    "properties": {"tenantId": other, "sku": {"family": "A", "name": "standard"}},
                },
            }
        )
        with pytest.raises(AdminError):
            async with database.sessions.begin() as session:
                await import_cloud(session, UUID(other), state)
