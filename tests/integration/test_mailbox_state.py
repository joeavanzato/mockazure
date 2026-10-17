from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.evidence.models import EvidenceSource
from mockazure.identity.types import PrincipalContext
from mockazure.providers.graph.errors import GraphError
from tests.integration.graph_support import graph_client


async def provision(client, tenant, user, **extra):
    response = await client.put(
        f"/_mock/tenants/{tenant}/mail/settings",
        json={"exchange_enabled": True, "audit_tier": "standard"},
    )
    assert response.status_code == 200, response.text
    response = await client.put(
        f"/_mock/tenants/{tenant}/mail/mailboxes/{user}",
        json={"primary_smtp": "reader@contoso.test", "alias": "reader", **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def actor(tenant, app, user=None, *, graph=False, write=False):
    return PrincipalContext(
        tenant,
        app,
        user,
        "delegated" if user else "application",
        "00000003-0000-0000-c000-000000000000" if graph else "https://outlook.office365.com",
        frozenset({"MailboxSettings.ReadWrite" if write else "MailboxSettings.Read"})
        if graph
        else frozenset({"Exchange.Manage" if user else "Exchange.ManageAsApp"}),
    )


async def test_mailbox_identity_and_scope(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        created = await provision(client, tenant, user)
        from mockazure.mail.authorization import require_mailbox_access
        from mockazure.mail.repository import resolve_mailbox

        second = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "other@contoso.test",
                "display_name": "Other",
                "password": "local-other-password",
            },
        )
        other = UUID(second.json()["id"])
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/mail/mailboxes/{other}",
                json={
                    "primary_smtp": "other@contoso.test",
                    "alias": "reader",
                },
            )
        ).status_code == 200
        async with database.sessions() as session:
            for identity in (str(user), "READER@CONTOSO.TEST", created["ExchangeGuid"]):
                box = await resolve_mailbox(session, tenant, identity)
                assert box.user_id == user
            with pytest.raises(GraphError, match="ambiguous"):
                await resolve_mailbox(session, tenant, "reader")
            box = await resolve_mailbox(session, tenant, str(user))
            await require_mailbox_access(
                session, actor(tenant, app, user, graph=True), box, "rules.list"
            )
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(
                    session, actor(tenant, app, other, graph=True), box, "rules.list"
                )
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, actor(tenant, app), box, "Get-Mailbox")
        grant = await client.post(
            f"/_mock/tenants/{tenant}/mail/grants",
            json={
                "client_id": str(app),
                "mailbox_user_id": str(user),
                "commands": ["Get-Mailbox"],
            },
        )
        assert grant.status_code == 201, grant.text
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            await require_mailbox_access(session, actor(tenant, app), box, "Get-Mailbox")
            other_box = await resolve_mailbox(session, tenant, str(other))
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, actor(tenant, app), other_box, "Get-Mailbox")
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, actor(tenant, app), box, "Get-InboxRule")
        assert (
            await client.delete(f"/_mock/tenants/{tenant}/mail/grants/{grant.json()['id']}")
        ).status_code == 204
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, actor(tenant, app), box, "Get-Mailbox")
        foreign = (
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
        async with database.sessions() as session:
            with pytest.raises(GraphError):
                await resolve_mailbox(session, UUID(foreign), str(user))
        from tests.integration.graph_support import headers_for

        headers = await headers_for(database, settings, tenant, app, None, {"User.ReadWrite.All"})
        assert (await client.delete(f"/v1.0/users/{user}", headers=headers)).status_code == 204
        async with database.sessions() as session:
            with pytest.raises(GraphError):
                await resolve_mailbox(session, tenant, created["ExchangeGuid"])


async def test_forwarding_mutation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        target = (
            await client.post(
                f"/_mock/tenants/{tenant}/identity/users",
                json={
                    "upn": "forward@contoso.test",
                    "display_name": "Forward target",
                    "password": "local-forward-password",
                },
            )
        ).json()["id"]
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/mail/mailboxes/{target}",
                json={
                    "primary_smtp": "forward@contoso.test",
                    "alias": "forward",
                },
            )
        ).status_code == 200
        assert (
            await client.post(
                f"/_mock/tenants/{tenant}/mail/grants",
                json={
                    "client_id": str(app),
                    "commands": ["Set-Mailbox"],
                },
            )
        ).status_code == 201
        from mockazure.mail.mailboxes import mutate_mailbox
        from mockazure.mail.repository import resolve_mailbox

        async with database.sessions.begin() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            result = await mutate_mailbox(
                session,
                actor(tenant, app),
                box,
                {
                    "ForwardingAddress": target,
                    "ForwardingSmtpAddress": "smtp:outside@example.test",
                    "DeliverToMailboxAndForward": True,
                },
                datetime.now(UTC),
            )
            assert result["ForwardingAddress"] == target
            assert result["ForwardingSmtpAddress"] == "smtp:outside@example.test"
            assert result["EffectiveForwardingAddress"] == "forward@contoso.test"
        async with database.sessions() as session:
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant,
                        EvidenceSource.kind == "mail",
                    )
                )
            )
            assert len(facts) == 1
            assert facts[0].facts["operation"] == "Set-Mailbox"
            assert facts[0].facts["after"]["ForwardingAddress"] == target
        with pytest.raises(RuntimeError):
            async with database.sessions.begin() as session:
                box = await resolve_mailbox(session, tenant, str(user))
                await mutate_mailbox(
                    session,
                    actor(tenant, app),
                    box,
                    {
                        "ForwardingAddress": None,
                    },
                    datetime.now(UTC),
                )
                raise RuntimeError("rollback probe")
        async with database.sessions() as session:
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant,
                        EvidenceSource.kind == "mail",
                    )
                )
            )
            assert len(facts) == 1


async def test_mailbox_statistics_derive_from_persisted_folders(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        value = await provision(
            client,
            tenant,
            user,
            folders=[
                {
                    "id": "inbox",
                    "name": "Inbox",
                    "path": "/Inbox",
                    "item_count": 12,
                    "size_bytes": 4000,
                },
                {
                    "id": "sent",
                    "name": "Sent Items",
                    "path": "/Sent Items",
                    "item_count": 3,
                    "size_bytes": 900,
                },
            ],
            cas={"ImapEnabled": False, "PopEnabled": True},
        )
        assert value["ExternalDirectoryObjectId"] == str(user)
        assert value["ExchangeGuid"] != str(user)
        from mockazure.mail.mailboxes import cas_view, mailbox_statistics
        from mockazure.mail.repository import resolve_mailbox

        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            stats = await mailbox_statistics(session, box)
            assert stats["ItemCount"] == 15
            assert stats["TotalItemSizeBytes"] == 4900
            assert (await cas_view(session, box))["ImapEnabled"] is False
            with pytest.raises(GraphError):
                await mailbox_statistics(session, box, archive=True)
        response = await client.put(
            f"/_mock/tenants/{tenant}/mail/mailboxes/{user}",
            json={
                "primary_smtp": "reader@contoso.test",
                "alias": "reader",
                "archive_enabled": True,
                "folders": [
                    {
                        "id": "archive-root",
                        "name": "Archive",
                        "path": "/",
                        "archive": True,
                        "item_count": 8,
                        "size_bytes": 15000,
                    }
                ],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["ExchangeGuid"] == value["ExchangeGuid"]
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            stats = await mailbox_statistics(session, box, archive=True)
            assert stats["ItemCount"] == 8
            assert stats["TotalItemSizeBytes"] == 15000
