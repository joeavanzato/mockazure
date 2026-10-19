"""Final review regressions for signed authority and vault fixture ownership."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from mockazure.keyvault.secret_models import Secret, SecretVersion
from tests.integration.graph_support import graph_client
from tests.integration.test_cloud_resource_inventory import VERSIONS, create
from tests.integration.test_keyvault_secrets import ARGS, provision_vault
from tests.integration.test_storage_authorization import provision
from tests.integration.test_table_workflows import batch, entity_url, table_call


def account_sas(key, *, service="t", resource="o", permissions="u"):
    args = {
        "sv": "2019-02-02" if service == "t" else "2026-02-06",
        "ss": service,
        "srt": resource,
        "sp": permissions,
        "se": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "spr": "https",
    }
    fields = ["sp", "ss", "srt", "st", "se", "sip", "spr", "sv"]
    if args["sv"] >= "2020-12-06":
        fields.append("ses")
    value = "signedaccount\n" + "\n".join(args.get(k, "") for k in fields) + "\n"
    args["sig"] = base64.b64encode(
        hmac.new(base64.b64decode(key), value.encode(), hashlib.sha256).digest()
    ).decode()
    return args


@pytest.mark.parametrize("method", ["PUT", "MERGE"])
@pytest.mark.parametrize("batched", [False, True])
async def test_account_sas_upsert_requires_add_and_update(database, settings, method, batched):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        await table_call(client, "POST", base + "Tables", key, body={"TableName": "Evidence"})
        client.headers.pop("Authorization", None)
        path = "Evidence(PartitionKey='p',RowKey='new')"

        async def mutate(permissions, etag=None):
            args = account_sas(key, permissions=permissions)
            if batched:
                return await client.post(
                    base + "$batch",
                    params=args,
                    headers={"Content-Type": "multipart/mixed; boundary=batch_test"},
                    content=batch(base, [(method, path, {"Flag": True}, etag)]),
                )
            return await client.request(
                method,
                base + path,
                params=args,
                json={"Flag": True},
                headers={"If-Match": etag} if etag else {},
            )

        for permissions in ["u", "a"]:
            denied = await mutate(permissions)
            assert denied.status_code == 403, denied.text
            assert "AuthorizationPermissionMismatch" in denied.text
        assert (
            await table_call(client, "GET", entity_url(base, "Evidence", "p", "new"), key)
        ).status_code == 404
        allowed = await mutate("au")
        assert allowed.status_code == (202 if batched else 204), allowed.text
        conditional = await mutate("u", "*")
        assert conditional.status_code == (202 if batched else 204), conditional.text
        if batched:
            assert "204 No Content" in allowed.text and "204 No Content" in conditional.text


@pytest.mark.parametrize("service,allowed,denied", [("t", "c", "s"), ("q", "s", "c")])
async def test_account_sas_namespace_listing_resource_type(
    database, settings, service, allowed, denied
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        client.headers.pop("Authorization", None)
        url = endpoints["table"] + "Tables" if service == "t" else endpoints["queue"]
        extra = {} if service == "t" else {"comp": "list"}
        for resource, status in [(allowed, 200), (denied, 403)]:
            result = await client.get(
                url,
                params=account_sas(key, service=service, resource=resource, permissions="l")
                | extra,
            )
            assert result.status_code == status, result.text
            if status == 403:
                assert "AuthorizationResourceTypeMismatch" in result.text


async def test_resource_fixture_removal_cannot_rebind_old_secrets(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, admin, principal, arm, base, head, policy = await provision_vault(
            client, database, settings, tenant, app, policies=True
        )
        assert (
            await client.put(
                base + "secrets/original",
                params=ARGS,
                headers=head,
                json={"value": "must-not-reattach"},
            )
        ).status_code == 200
        resource_path = f"/_mock/tenants/{tenant}/cloud/resourceState"
        fixture = (await client.get(resource_path)).json()
        removed = await client.put(resource_path, json={"resources": []})
        assert removed.status_code == 200, removed.text
        async with database.sessions() as session:
            for model in (Secret, SecretVersion):
                assert (
                    await session.scalar(
                        select(func.count()).select_from(model).where(model.tenant_id == tenant)
                    )
                    == 0
                )
        state_path = f"/_mock/tenants/{tenant}/cloud/state"
        exported = (await client.get(state_path)).json()
        assert exported["keyvault"]["secrets"] == []
        restored = await client.put(state_path, json=exported)
        assert restored.status_code == 200, restored.text
        await create(
            client,
            path,
            fixture["resources"][0]["configuration"],
            arm,
            VERSIONS["Microsoft.KeyVault"],
        )
        assert (
            await client.get(base + "secrets/original", params=ARGS, headers=head)
        ).status_code == 404
        fresh = await client.put(
            base + "secrets/original",
            params=ARGS,
            headers=head,
            json={"value": "fresh-owner-value"},
        )
        assert fresh.status_code == 200, fresh.text
        assert (await client.get(base + "secrets/original", params=ARGS, headers=head)).json()[
            "value"
        ] == "fresh-owner-value"
