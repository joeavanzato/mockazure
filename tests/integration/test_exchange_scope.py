from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_exchange_mail import command
from tests.integration.test_graph_mail_rules import body
from tests.integration.test_mailbox_state import provision


async def test_exchange_hidden_rules_and_delegated_recipient_scope(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"Exchange.Manage"},
            kind="delegated",
            audience="https://outlook.office365.com",
        )
        assert (await command(client, tenant, headers, "Get-InboxRule")).status_code == 403
        grant = await client.post(
            f"/_mock/tenants/{tenant}/mail/grants",
            json={
                "user_id": str(user),
                "mailbox_user_id": str(user),
                "commands": ["Get-InboxRule", "Remove-InboxRule", "Get-Mailbox"],
            },
        )
        assert grant.status_code == 201, grant.text
        seed = f"/_mock/tenants/{tenant}/mail/mailboxes/{user}/rules"
        hidden = await client.post(seed, json={"rule": body("Hidden"), "hidden": True})
        readonly = await client.post(seed, json={"rule": body("Readonly"), "read_only": True})
        assert hidden.status_code == readonly.status_code == 201
        listed = await command(client, tenant, headers, "Get-InboxRule")
        assert listed.status_code == 200, listed.text
        assert [r["Name"] for r in listed.json()["value"]] == ["Readonly"]
        listed = await command(client, tenant, headers, "Get-InboxRule", {"IncludeHidden": True})
        assert {r["Name"] for r in listed.json()["value"]} == {"Hidden", "Readonly"}
        denied = await command(
            client, tenant, headers, "Remove-InboxRule", {"Identity": readonly.json()["id"]}
        )
        assert denied.status_code == 403
        other = (
            await client.post(
                f"/_mock/tenants/{tenant}/identity/users",
                json={
                    "upn": "another@contoso.test",
                    "display_name": "Another",
                    "password": "local-other-password",
                },
            )
        ).json()["id"]
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/mail/mailboxes/{other}",
                json={
                    "primary_smtp": "another@contoso.test",
                    "alias": "another",
                },
            )
        ).status_code == 200
        assert (
            await command(client, tenant, headers, "Get-InboxRule", {"Mailbox": other})
        ).status_code == 403
        scoped = await command(client, tenant, headers, "Get-Mailbox", {"ResultSize": "Unlimited"})
        assert scoped.status_code == 200, scoped.text
        assert [r["ExternalDirectoryObjectId"] for r in scoped.json()["value"]] == [str(user)]
        assert (
            await client.delete(f"/_mock/tenants/{tenant}/mail/grants/{grant.json()['id']}")
        ).status_code == 204
        assert (await command(client, tenant, headers, "Get-InboxRule")).status_code == 403
