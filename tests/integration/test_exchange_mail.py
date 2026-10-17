from sqlalchemy import select

from mockazure.evidence.models import EvidenceSource
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_mailbox_state import provision


async def command(client, tenant, headers, name, parameters=None):
    return await client.post(
        f"/adminapi/v2.0/{tenant}/InvokeCommand",
        headers=headers,
        json={"CmdletInput": {"CmdletName": name, "Parameters": parameters or {}}},
    )


async def exchange_headers(database, settings, tenant, app):
    return await headers_for(
        database,
        settings,
        tenant,
        app,
        None,
        {"Exchange.ManageAsApp"},
        audience="https://outlook.office365.com",
    )


async def test_exchange_configuration_and_rules(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(
            client,
            tenant,
            user,
            folders=[
                {"id": "inbox", "name": "Inbox", "path": "/Inbox"},
                {"id": "review", "name": "Review", "path": "/Review"},
            ],
        )
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Global Administrator"
            )
        ).status_code == 200
        headers = await exchange_headers(database, settings, tenant, app)
        result = await command(client, tenant, headers, "Get-Mailbox", {"Identity": str(user)})
        assert result.status_code == 200, result.text
        assert result.json()["value"][0]["ExternalDirectoryObjectId"] == str(user)
        changed = await command(
            client,
            tenant,
            headers,
            "Set-Mailbox",
            {
                "Identity": str(user),
                "ForwardingSmtpAddress": "smtp:external@example.test",
                "DeliverToMailboxAndForward": True,
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["value"] == []
        created = await command(
            client,
            tenant,
            headers,
            "New-InboxRule",
            {
                "Mailbox": str(user),
                "Name": "Mailbox rule",
                "Priority": 1,
                "ForwardTo": ["external@example.test"],
                "SubjectContainsWords": ["invoice"],
                "StopProcessingRules": True,
            },
        )
        assert created.status_code == 200, created.text
        rule = created.json()["value"][0]
        identity = rule["Identity"]
        assert rule["Name"] == "Mailbox rule"
        rules = await command(client, tenant, headers, "Get-InboxRule", {"Mailbox": str(user)})
        assert rules.status_code == 200
        assert rules.json()["value"][0]["RuleIdentity"] == rule["RuleIdentity"]
        disabled = await command(
            client,
            tenant,
            headers,
            "Disable-InboxRule",
            {
                "Mailbox": str(user),
                "Identity": identity,
            },
        )
        assert disabled.status_code == 200, disabled.text
        async with database.sessions() as session:
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "mail"
                    )
                )
            )
            assert "Disable-InboxRule" in {row.facts["operation"] for row in facts}
        graph_headers = await headers_for(
            database, settings, tenant, app, None, {"MailboxSettings.Read"}
        )
        rows = (
            await client.get(
                f"/v1.0/users/{user}/mailFolders/inbox/messageRules", headers=graph_headers
            )
        ).json()["value"]
        assert rows[0]["isEnabled"] is False
        assert rows[0]["conditions"]["subjectContains"] == ["invoice"]
        assert (
            await command(
                client,
                tenant,
                headers,
                "Remove-InboxRule",
                {
                    "Mailbox": str(user),
                    "Identity": identity,
                },
            )
        ).status_code == 200
        sweep = await command(
            client,
            tenant,
            headers,
            "New-SweepRule",
            {
                "Mailbox": str(user),
                "Name": "Sweep",
                "Provider": "Exchange16",
                "Sender": "external@example.test",
                "SourceFolder": "inbox",
                "DestinationFolder": "review",
                "KeepLatest": True,
            },
        )
        assert sweep.status_code == 200, sweep.text
        sweep_id = sweep.json()["value"][0]["Identity"]
        sweeps = await command(client, tenant, headers, "Get-SweepRule", {"Mailbox": str(user)})
        assert sweeps.status_code == 200
        assert sweeps.json()["value"][0]["Name"] == "Sweep"
        assert (
            await command(
                client,
                tenant,
                headers,
                "Remove-SweepRule",
                {
                    "Mailbox": str(user),
                    "Identity": sweep_id,
                },
            )
        ).status_code == 200
        unsupported = await command(
            client,
            tenant,
            headers,
            "Get-Mailbox",
            {
                "Identity": str(user),
                "MadeUpSwitch": True,
            },
        )
        assert unsupported.status_code == 400


async def test_exchange_reader_and_scope_do_not_grant_inbox_rules(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        roles = f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/"
        assert (await client.put(roles + "Global Reader")).status_code == 200
        headers = await exchange_headers(database, settings, tenant, app)
        assert (
            await command(client, tenant, headers, "Get-Mailbox", {"Identity": str(user)})
        ).status_code == 200
        denied = await command(client, tenant, headers, "Get-InboxRule", {"Mailbox": str(user)})
        assert denied.status_code == 403, denied.text
        assert (await client.delete(roles + "Global Reader")).status_code == 200
        grant = await client.post(
            f"/_mock/tenants/{tenant}/mail/grants",
            json={
                "client_id": str(app),
                "mailbox_user_id": str(user),
                "commands": ["Get-InboxRule"],
            },
        )
        assert grant.status_code == 201
        assert (
            await command(client, tenant, headers, "Get-InboxRule", {"Mailbox": str(user)})
        ).status_code == 200
        assert (
            await client.delete(f"/_mock/tenants/{tenant}/mail/grants/{grant.json()['id']}")
        ).status_code == 204
        assert (
            await command(client, tenant, headers, "Get-InboxRule", {"Mailbox": str(user)})
        ).status_code == 403
