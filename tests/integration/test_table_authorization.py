"""Table data grants and signed ranges remain current on every continuation and batch."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from mockazure.identity.models import ServicePrincipal
from mockazure.providers.arm.models import RoleAssignment
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_arm_authorization import assign
from tests.integration.test_storage_authorization import ARGS, provision
from tests.integration.test_table_workflows import batch, entity_url, table_call


def sas(key, **changes):
    now = datetime.now(UTC)
    args = {
        "sv": "2019-02-02",
        "tn": "evidence",
        "sp": "r",
        "st": (now - timedelta(minutes=1)).isoformat(),
        "se": (now + timedelta(minutes=5)).isoformat(),
        "spr": "https",
    } | changes
    value = "\n".join(
        [args.get(k, "") for k in ("sp", "st", "se")]
        + ["/table/signedaccount/evidence"]
        + [args.get(k, "") for k in ("si", "sip", "spr", "sv", "spk", "srk", "epk", "erk")]
    )
    args["sig"] = base64.b64encode(
        hmac.new(base64.b64decode(key), value.encode(), hashlib.sha256).digest()
    ).decode()
    return args


async def test_table_current_grants_and_paging(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        account, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        await table_call(client, "POST", base + "Tables", key, body={"TableName": "Evidence"})
        for row in ("1", "2", "3"):
            assert (
                await table_call(
                    client,
                    "POST",
                    base + "Evidence",
                    key,
                    body={"PartitionKey": "p", "RowKey": row},
                )
            ).status_code == 201
        oauth = await headers_for(
            database, settings, tenant, app, None, [], audience="https://storage.azure.com/"
        )
        head = oauth | {"x-ms-version": "2019-02-02", "Accept": "application/json"}
        assert (await client.get(base + "Evidence()", headers=head)).status_code == 403
        assert (
            await client.get(base + "Evidence()", headers=arm | {"x-ms-version": "2019-02-02"})
        ).status_code == 403
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(ServicePrincipal.client_id == app)
            )
        admin = f"/_mock/tenants/{tenant}/subscriptions/{account.split('/')[2]}"
        await assign(client, admin, sp, account, "Storage Table Data Reader")
        first = await client.get(base + "Evidence()", params={"$top": "1"}, headers=head)
        assert first.status_code == 200, first.text
        params = {
            "$top": "1",
            "NextPartitionKey": first.headers["x-ms-continuation-NextPartitionKey"],
            "NextRowKey": first.headers["x-ms-continuation-NextRowKey"],
        }
        assert (
            await client.get(
                base + "Evidence()", params=params | {"$filter": "RowKey eq '1'"}, headers=head
            )
        ).status_code == 400
        assert (
            await client.post(
                base + "Evidence", headers=head, json={"PartitionKey": "p", "RowKey": "4"}
            )
        ).status_code == 403
        async with database.sessions.begin() as session:
            await session.execute(
                delete(RoleAssignment).where(
                    RoleAssignment.tenant_id == tenant,
                    RoleAssignment.scope_key == account.lower(),
                    RoleAssignment.client_id == app,
                )
            )
        assert (
            await client.get(base + "Evidence()", params=params, headers=head)
        ).status_code == 403


async def test_table_service_sas_key_ranges_and_revocation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        account, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        await table_call(client, "POST", base + "Tables", key, body={"TableName": "Evidence"})
        for row in ("1", "2", "3"):
            await table_call(
                client, "POST", base + "Evidence", key, body={"PartitionKey": "p", "RowKey": row}
            )
        client.headers.pop("Authorization", None)
        args = sas(key, spk="p", srk="2", epk="p", erk="2")
        result = await client.get(
            base + "Evidence()", params=args, headers={"Accept": "application/json"}
        )
        assert result.status_code == 200, result.text
        assert [v["RowKey"] for v in result.json()["value"]] == ["2"]
        assert (
            await client.get(entity_url(base, "Evidence", "p", "1"), params=args)
        ).status_code == 403
        assert (
            await client.get(base + "Evidence()", params=args | {"erk": "3"})
        ).status_code == 403
        assert (await client.get(base + "Tables", params=args)).status_code == 403
        write = sas(key, sp="raud", spk="p", srk="2", epk="p", erk="2")
        # Every member is checked before the changeset can commit.
        ops = [
            ("PATCH", "Evidence(PartitionKey='p',RowKey='2')", {"Flag": True}, "*"),
            ("PATCH", "Evidence(PartitionKey='p',RowKey='3')", {"Flag": True}, "*"),
        ]
        denied = await client.post(
            base + "$batch",
            params=write,
            headers={"Content-Type": "multipart/mixed; boundary=batch_test"},
            content=batch(base, ops),
        )
        assert denied.status_code == 403, denied.text
        read = await table_call(client, "GET", entity_url(base, "Evidence", "p", "2"), key)
        assert "Flag" not in read.json()
        assert (
            await client.post(
                account + "/regenerateKey", params=ARGS, headers=arm, json={"keyName": "key1"}
            )
        ).status_code == 200
        assert (await client.get(base + "Evidence()", params=args)).status_code == 403


async def test_table_no_metadata_insert_and_explicit_option_errors(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        await table_call(client, "POST", base + "Tables", key, body={"TableName": "Evidence"})
        result = await table_call(
            client,
            "POST",
            base + "Evidence",
            key,
            body={
                "PartitionKey": "p",
                "RowKey": "1",
                "N@odata.type": "Edm.Int64",
                "N": "9007199254740993",
            },
            headers={"Accept": "application/json;odata=nometadata"},
        )
        assert result.status_code == 201, result.text
        assert not any("@odata" in k or k.startswith("odata.") for k in result.json())
        for params in (
            {"$expand": "Anything"},
            {"$top": "0"},
            {"$filter": "N add 1 eq 2"},
            {"NextRowKey": "orphan"},
        ):
            assert (
                await table_call(client, "GET", base + "Evidence()", key, params=params)
            ).status_code == 400
        for params in ({"$filter": "RowKey eq '1'"}, {"$top": "1"}, {"NextTableName": "bad"}):
            assert (
                await table_call(
                    client, "GET", entity_url(base, "Evidence", "p", "1"), key, params=params
                )
            ).status_code == 400


async def test_table_batch_update_sas_needs_inner_if_match(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["table"]
        await table_call(client, "POST", base + "Tables", key, body={"TableName": "Evidence"})
        await table_call(
            client, "POST", base + "Evidence", key, body={"PartitionKey": "p", "RowKey": "1"}
        )
        client.headers.pop("Authorization", None)
        args = sas(key, sp="u")
        ops = [("PATCH", "Evidence(PartitionKey='p',RowKey='1')", {"Flag": True}, "*")]
        result = await client.post(
            base + "$batch",
            params=args,
            headers={"Content-Type": "multipart/mixed; boundary=batch_test"},
            content=batch(base, ops),
        )
        assert result.status_code == 202 and "204 No Content" in result.text, result.text
