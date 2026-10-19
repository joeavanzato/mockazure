"""Queue operations honor current data roles and narrowly scoped SAS permissions."""

from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

from azure.storage.queue import (
    AccountSasPermissions,
    QueueSasPermissions,
    ResourceTypes,
    generate_account_sas,
    generate_queue_sas,
)
from sqlalchemy import delete, select

from mockazure.identity.models import ServicePrincipal
from mockazure.providers.arm.models import RoleAssignment
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_arm_authorization import assign
from tests.integration.test_queue_workflows import VERSION, message, messages, queue_call
from tests.integration.test_storage_authorization import ARGS, provision


async def test_queue_rbac_sender_reader_processor_and_revocation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        account, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["queue"] + "permissions"
        assert (await queue_call(client, "PUT", url, key)).status_code == 201
        oauth = await headers_for(
            database, settings, tenant, app, None, [], audience="https://storage.azure.com/"
        )
        head = oauth | {"x-ms-version": VERSION, "Content-Type": "application/xml"}
        assert (
            await client.post(url + "/messages", headers=head, content=message("denied"))
        ).status_code == 403
        assert (
            await client.get(url + "/messages", headers=arm | {"x-ms-version": VERSION})
        ).status_code == 403
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(ServicePrincipal.client_id == app)
            )
        admin = f"/_mock/tenants/{tenant}/subscriptions/{account.split('/')[2]}"
        await assign(client, admin, sp, account, "Storage Queue Data Message Sender")
        assert (
            await client.post(url + "/messages", headers=head, content=message("sender"))
        ).status_code == 201
        assert (
            await client.get(url + "/messages", headers=head, params={"peekonly": "true"})
        ).status_code == 403
        await assign(client, admin, sp, account, "Storage Queue Data Reader")
        assert (
            await client.get(url + "/messages", headers=head, params={"peekonly": "true"})
        ).status_code == 200
        assert (await client.get(url + "/messages", headers=head)).status_code == 403
        await assign(client, admin, sp, account, "Storage Queue Data Message Processor")
        received = messages(await client.get(url + "/messages", headers=head))[0]
        target = url + "/messages/" + received["MessageId"]
        assert (
            await client.put(
                target,
                headers=head,
                params={"popreceipt": received["PopReceipt"], "visibilitytimeout": "0"},
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
            await client.delete(target, headers=head, params={"popreceipt": received["PopReceipt"]})
        ).status_code == 403
        await assign(client, admin, sp, account, "Storage Queue Data Contributor")
        assert (
            await client.put(
                target,
                headers=head,
                params={"popreceipt": received["PopReceipt"], "visibilitytimeout": "0"},
            )
        ).status_code == 204
        assert (await client.delete(url + "/messages", headers=head)).status_code == 204


async def test_queue_sas_boundaries_and_key_rotation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        account, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        base = endpoints["queue"]
        for name in ("one-queue", "two-queue"):
            assert (await queue_call(client, "PUT", base + name, key)).status_code == 201
        url = base + "one-queue"
        sent = messages(
            await queue_call(client, "POST", url + "/messages", key, body=message("sensitive"))
        )[0]
        head = {"Authorization": "", "x-ms-version": VERSION, "Content-Type": "application/xml"}
        expiry = datetime.now(UTC) + timedelta(minutes=5)
        sas = generate_queue_sas(
            "signedaccount", "one-queue", key, QueueSasPermissions(read=True), expiry
        )
        check = await client.get(url + "/messages?peekonly=true&" + sas, headers=head)
        assert check.status_code == 200, check.text
        assert (await client.get(url + "?comp=metadata&" + sas, headers=head)).status_code == 200
        assert (await client.get(url + "/messages?" + sas, headers=head)).status_code == 403
        assert (
            await client.get(base + "two-queue/messages?peekonly=true&" + sas, headers=head)
        ).status_code == 403
        from urllib.parse import urlencode

        params = urlencode({"popreceipt": sent["PopReceipt"], "visibilitytimeout": "0"})
        writer = generate_account_sas(
            "signedaccount",
            key,
            ResourceTypes(object=True),
            AccountSasPermissions(write=True),
            expiry,
        )
        denied = await client.put(
            url + "/messages/" + sent["MessageId"] + "?" + writer + "&" + params, headers=head
        )
        assert denied.status_code == 403
        updater = generate_account_sas(
            "signedaccount",
            key,
            ResourceTypes(object=True),
            AccountSasPermissions(update=True),
            expiry,
        )
        # Append to preserve SAS; httpx params would replace the URL query.
        from urllib.parse import urlencode

        params = urlencode({"popreceipt": sent["PopReceipt"], "visibilitytimeout": "0"})
        updated = await client.put(
            url + "/messages/" + sent["MessageId"] + "?" + updater + "&" + params, headers=head
        )
        assert updated.status_code == 204, updated.text
        rotated = await client.post(
            account + "/regenerateKey", params=ARGS, headers=arm, json={"keyName": "key1"}
        )
        assert rotated.status_code == 200
        assert (
            await client.get(url + "/messages?peekonly=true&" + sas, headers=head)
        ).status_code == 403


async def test_queue_bad_inputs_and_receipt_errors(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, _, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["queue"] + "validation"
        assert (await queue_call(client, "PUT", url, key)).status_code == 201
        for body in (
            (
                b'<!DOCTYPE a [<!ENTITY x "secret">]>'
                b"<QueueMessage><MessageText>&x;</MessageText></QueueMessage>"
            ),
            b"<QueueMessage><MessageText/><MessageText/></QueueMessage>",
            b"<QueueMessage><MessageText><Nested/></MessageText></QueueMessage>",
        ):
            response = await queue_call(client, "POST", url + "/messages", key, body=body)
            assert response.status_code == 400
        for options in ({"peekonly": "false"}, {"peekonly": "true", "visibilitytimeout": "30"}):
            assert (
                await queue_call(client, "GET", url + "/messages", key, options)
            ).status_code == 400
        assert (await queue_call(client, "GET", url, key, {"comp": "acl"})).status_code == 501
        result = await queue_call(
            client, "DELETE", url + "/messages/not-a-uuid", key, {"popreceipt": "unknown"}
        )
        assert (
            result.status_code == 404
            and ET.fromstring(result.content).findtext("Code") == "MessageNotFound"
        )
