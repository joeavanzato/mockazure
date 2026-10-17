from uuid import uuid4

import pytest

from tests.integration.graph_support import graph_client
from tests.integration.test_exchange_mail import command, exchange_headers
from tests.integration.test_mailbox_state import provision


@pytest.mark.parametrize("family", ["Inbox", "Sweep"])
@pytest.mark.parametrize("verb", ["Get", "Remove"])
async def test_qualified_rule_identity_cannot_override_explicit_mailbox(
    database, settings, family, verb
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        folders = [
            {"id": "inbox", "name": "Inbox", "path": "/Inbox"},
            {"id": "review", "name": "Review", "path": "/Review"},
        ]
        await provision(client, tenant, user, folders=folders)
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Global Administrator"
            )
        ).status_code == 200
        headers = await exchange_headers(database, settings, tenant, app)
        other = (
            await client.post(
                f"/_mock/tenants/{tenant}/identity/users",
                json={
                    "upn": "other@contoso.test",
                    "display_name": "Other",
                    "password": "local-other-password",
                },
            )
        ).json()["id"]
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/mail/mailboxes/{other}",
                json={"primary_smtp": "other@contoso.test", "alias": "other", "folders": folders},
            )
        ).status_code == 200
        extra = (
            {
                "Provider": "Exchange16",
                "Sender": "sender@example.test",
                "SourceFolder": "inbox",
                "DestinationFolder": "review",
            }
            if family == "Sweep"
            else {"MarkAsRead": True}
        )
        for mailbox in (str(user), other):
            response = await command(
                client,
                tenant,
                headers,
                f"New-{family}Rule",
                {"Mailbox": mailbox, "Name": "Shared", **extra},
            )
            assert response.status_code == 200, response.text
        # A real mailbox in a foreign tenant must not act as a local prefix.
        foreign_tenant = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "foreign",
                    "display_name": "Foreign",
                    "domain": "foreign.test",
                    "licences": [],
                },
            )
        ).json()["id"]
        assert (
            await client.put(
                f"/_mock/tenants/{foreign_tenant}/mail/settings",
                json={"exchange_enabled": True, "audit_tier": "standard"},
            )
        ).status_code == 200
        foreign = (
            await client.post(
                f"/_mock/tenants/{foreign_tenant}/identity/users",
                json={
                    "upn": "other@foreign.test",
                    "display_name": "Foreign",
                    "password": "local-foreign-password",
                },
            )
        ).json()["id"]
        assert (
            await client.put(
                f"/_mock/tenants/{foreign_tenant}/mail/mailboxes/{foreign}",
                json={"primary_smtp": "other@foreign.test", "alias": "foreign"},
            )
        ).status_code == 200
        for prefix, status in ((other, 400), (str(uuid4()), 404), (foreign, 404)):
            response = await command(
                client,
                tenant,
                headers,
                f"{verb}-{family}Rule",
                {"Mailbox": str(user), "Identity": prefix + "\\Shared"},
            )
            assert response.status_code == status, response.text
        for mailbox in (str(user), other):
            response = await command(
                client, tenant, headers, f"Get-{family}Rule", {"Mailbox": mailbox}
            )
            assert len(response.json()["value"]) == 1
        response = await command(
            client,
            tenant,
            headers,
            f"{verb}-{family}Rule",
            {"Mailbox": str(user), "Identity": "READER@CONTOSO.TEST\\Shared"},
        )
        assert response.status_code == 200, response.text
