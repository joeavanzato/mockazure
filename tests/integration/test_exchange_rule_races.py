import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.evidence.models import EvidenceSource
from mockazure.mail.models import Mailbox, MailRule, MailRuleVersion
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.rules import mutate_rule
from mockazure.providers.exchange.commands import invoke_command
from tests.integration.graph_support import graph_client
from tests.integration.test_exchange_mail import command, exchange_headers
from tests.integration.test_mailbox_state import actor, provision


@pytest.mark.parametrize("first_graph", [False, True])
async def test_rule_merge_serializes_before_loading_and_refreshes_history(
    database, settings, monkeypatch, first_graph
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await provision(client, tenant, user)
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Global Administrator"
            )
        ).status_code == 200
        headers = await exchange_headers(database, settings, tenant, app)
        response = await command(
            client,
            tenant,
            headers,
            "New-InboxRule",
            {"Mailbox": str(user), "Name": "Concurrent", "MarkAsRead": False},
        )
        assert response.status_code == 200, response.text
        rule_id = response.json()["value"][0]["RuleIdentity"]
        reached = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]
        sessions = {}
        refresh = AsyncSession.refresh

        async def gated_refresh(session, instance, *args, **kwargs):
            if isinstance(instance, Mailbox) and kwargs.get("with_for_update"):
                if id(session) not in sessions:
                    index = len(sessions)
                    sessions[id(session)] = index
                    reached[index].set()
                    await asyncio.wait_for(release[index].wait(), 15)
            return await refresh(session, instance, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "refresh", gated_refresh)

        async def change(index):
            async with database.sessions.begin() as session:
                # Keep an old ORM identity alive: the lock must also refresh rule rows.
                stale = await session.get(MailRule, (tenant, user, rule_id))
                assert stale.payload["actions"]["markAsRead"] is False
                if index == 0 and first_graph:
                    box = await resolve_mailbox(session, tenant, str(user))
                    await mutate_rule(
                        session,
                        actor(tenant, app, graph=True, write=True),
                        box,
                        "update",
                        rule_id,
                        {"actions": {"markAsRead": True}},
                        datetime.now(UTC),
                    )
                else:
                    await invoke_command(
                        session,
                        actor(tenant, app),
                        "Set-InboxRule",
                        {
                            "Mailbox": str(user),
                            "Identity": rule_id,
                            **(
                                {"MarkAsRead": True}
                                if index == 0
                                else {"StopProcessingRules": True}
                            ),
                        },
                        datetime.now(UTC),
                    )

        first = asyncio.create_task(change(0))
        await asyncio.wait_for(reached[0].wait(), 15)
        second = asyncio.create_task(change(1))
        try:
            await asyncio.wait_for(reached[1].wait(), 15)
            release[0].set()
            await asyncio.wait_for(first, 15)
            release[1].set()
            await asyncio.wait_for(second, 15)
        finally:
            for event in release:
                event.set()
            await asyncio.gather(first, second, return_exceptions=True)
        async with database.sessions() as session:
            row = await session.get(MailRule, (tenant, user, rule_id))
            assert row.payload["actions"] == {"markAsRead": True, "stopProcessingRules": True}
            versions = list(
                await session.scalars(
                    select(MailRuleVersion)
                    .where(MailRuleVersion.tenant_id == tenant, MailRuleVersion.id == rule_id)
                    .order_by(MailRuleVersion.valid_from)
                )
            )
            assert len(versions) == 3
            assert versions[1].payload["actions"] == {"markAsRead": True}
            assert versions[2].payload["actions"] == row.payload["actions"]
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "mail"
                    )
                )
            )
            last = next(
                f.facts
                for f in facts
                if f.facts["after"].get("actions", {}).get("stopProcessingRules")
            )
            assert last["before"]["actions"] == {"markAsRead": True}
            assert last["after"]["actions"] == row.payload["actions"]
