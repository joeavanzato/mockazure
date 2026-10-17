from dataclasses import replace
from uuid import UUID

import pytest

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.mail.authorization import require_mailbox_access
from mockazure.mail.repository import resolve_mailbox
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_mailbox_state import actor, provision


@pytest.mark.parametrize("mode", ["application", "delegated"])
async def test_current_exchange_roles_entitlements_and_disabled_user(database, settings, mode):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        caller = actor(tenant, app, user if mode == "delegated" else None)
        owner = f"users/{user}" if mode == "delegated" else f"applications/{app}"
        roles = f"/_mock/tenants/{tenant}/identity/{owner}/roles/"
        assert (await client.put(roles + "Global Reader")).status_code == 200
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            await require_mailbox_access(session, caller, box, "Get-Mailbox")
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, caller, box, "Get-InboxRule")
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(
                    session, replace(caller, permissions=frozenset()), box, "Get-Mailbox"
                )
        assert (await client.delete(roles + "Global Reader")).status_code == 200
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, caller, box, "Get-Mailbox")
        assert (await client.put(roles + "Exchange Administrator")).status_code == 200
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            await require_mailbox_access(session, caller, box, "Get-InboxRule")
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/mail/settings",
                json={
                    "exchange_enabled": False,
                    "audit_tier": "standard",
                },
            )
        ).status_code == 200
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, caller, box, "Get-Mailbox")


async def test_mail_management_requires_auth_and_rejects_foreign_identity(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        base = f"/_mock/tenants/{tenant}/mail"
        assert (
            await client.put(
                base + "/settings",
                headers={"Authorization": "Bearer invalid"},
                json={"exchange_enabled": True},
            )
        ).status_code == 401
        await provision(client, tenant, user)
        assert (
            await client.post(
                base + "/grants",
                json={
                    "client_id": str(app),
                    "commands": ["Invoke-Expression"],
                },
            )
        ).status_code == 422
        assert (
            await client.put(
                base + f"/mailboxes/{user}",
                json={
                    "primary_smtp": "reader@contoso.test",
                    "alias": "reader",
                    "archive_enabled": False,
                    "folders": [{"id": "a", "name": "Archive", "path": "/", "archive": True}],
                },
            )
        ).status_code == 422
        assert (
            await client.put(
                base + f"/mailboxes/{user}",
                json={
                    "primary_smtp": "reader@contoso.test",
                    "alias": "reader",
                    "cas": {"ImapEnabled": "false"},
                },
            )
        ).status_code == 422
        foreign = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "foreign",
                    "display_name": "Foreign",
                    "domain": "foreign.test",
                },
            )
        ).json()["id"]
        assert (
            await client.put(
                f"/_mock/tenants/{foreign}/mail/settings",
                json={
                    "exchange_enabled": True,
                },
            )
        ).status_code == 200
        assert (
            await client.put(
                f"/_mock/tenants/{foreign}/mail/mailboxes/{user}",
                json={
                    "primary_smtp": "foreign@foreign.test",
                    "alias": "foreign",
                },
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/_mock/tenants/{foreign}/mail/grants",
                json={
                    "client_id": str(app),
                    "commands": ["Get-Mailbox"],
                },
            )
        ).status_code == 404
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"User.ReadWrite.All", "User.EnableDisableAccount.All", "User.Read.All"},
        )
        assert (
            await client.patch(
                f"/v1.0/users/{user}",
                headers=headers,
                json={
                    "userPrincipalName": "renamed@contoso.test",
                    "accountEnabled": False,
                },
            )
        ).status_code == 204
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, "renamed@contoso.test")
            assert box.user_id == user
            await require_mailbox_access(session, actor(tenant, app, graph=True), box, "rules.list")
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(
                    session, actor(tenant, app, user, graph=True), box, "rules.list"
                )
        assert (
            await client.put(
                base + f"/mailboxes/{UUID(str(user))}",
                json={
                    "primary_smtp": "new@contoso.test",
                    "alias": "new",
                },
            )
        ).status_code == 200
