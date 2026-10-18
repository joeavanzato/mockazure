"""Review regressions: ambiguous trustees never grant and exports stay restorable."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from mockazure.directory.models import Group
from mockazure.evidence.models import EvidenceSource
from mockazure.identity.models import User
from mockazure.mail.delegation import has_delegation
from mockazure.mail.delegation_models import MailboxDelegation
from tests.integration.graph_support import graph_client, persisted_group
from tests.integration.test_exchange_mail import command
from tests.integration.test_exchange_recipients import setup
from tests.integration.test_mailbox_state import provision


@pytest.mark.parametrize("collision_kind", ["user", "group"])
async def test_alias_display_collision_cannot_mutate_authority(database, settings, collision_kind):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        alias = "delegate-alias@contoso.test"
        await provision(
            client,
            tenant,
            delegate,
            primary_smtp="delegate@contoso.test",
            alias="delegate",
            aliases=[alias],
        )
        async with database.sessions.begin() as session:
            if collision_kind == "user":
                collision = await session.get(User, owner)
            else:
                collision = await persisted_group(session, tenant)
            collision.display_name = alias
        async with database.sessions() as session:
            before = await session.scalar(
                select(func.count())
                .select_from(EvidenceSource)
                .where(EvidenceSource.tenant_id == tenant)
            )
        for name, values in [
            ("Add-MailboxPermission", {"User": alias, "AccessRights": ["FullAccess"]}),
            ("Add-RecipientPermission", {"Trustee": alias, "AccessRights": ["SendAs"]}),
            ("Set-Mailbox", {"GrantSendOnBehalfTo": [alias]}),
        ]:
            result = await command(
                client, tenant, headers, name, {"Identity": str(owner), **values}
            )
            assert result.status_code == 400, result.text
        async with database.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(MailboxDelegation)
                    .where(MailboxDelegation.tenant_id == tenant)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(EvidenceSource)
                    .where(EvidenceSource.tenant_id == tenant)
                )
                == before
            )
        # Multiple identity forms for the same principal are not ambiguous.
        async with database.sessions.begin() as session:
            collision = await session.get(User if collision_kind == "user" else Group, collision.id)
            collision.display_name = "No collision"
        result = await command(
            client,
            tenant,
            headers,
            "Add-MailboxPermission",
            {"Identity": str(owner), "User": alias, "AccessRights": ["FullAccess"]},
        )
        assert result.status_code == 200, result.text


@pytest.mark.parametrize("deleted_kind", ["user", "group"])
async def test_deleted_principal_export_roundtrips_live_grants(database, settings, deleted_kind):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        async with database.sessions.begin() as session:
            doomed = (
                await session.get(User, delegate)
                if deleted_kind == "user"
                else await persisted_group(session, tenant)
            )
            doomed_id = doomed.id
        for principal in [owner, doomed_id]:
            result = await command(
                client,
                tenant,
                headers,
                "Add-MailboxPermission",
                {"Identity": str(owner), "User": str(principal), "AccessRights": ["FullAccess"]},
            )
            assert result.status_code == 200, result.text
        async with database.sessions.begin() as session:
            doomed = await session.get(User if deleted_kind == "user" else Group, doomed_id)
            doomed.deleted_at = datetime.now(UTC)
        path = f"/_mock/tenants/{tenant}/mail/delegationState"
        exported = await client.get(path)
        assert exported.status_code == 200, exported.text
        restored = await client.put(path, json=exported.json())
        assert restored.status_code == 200, restored.text
        assert restored.json() == exported.json()
        assert {row["principal_id"] for row in exported.json()["grants"]} == {str(owner)}
        async with database.sessions() as session:
            assert await has_delegation(session, tenant, owner, owner, "FullAccess")
            assert not await has_delegation(session, tenant, owner, doomed_id, "FullAccess")
