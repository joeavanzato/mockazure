"""Named activity actors authenticate through local authorization-code endpoints."""

from uuid import UUID

import pytest

from mockazure.admin.errors import AdminError
from mockazure.identity.models import User
from mockazure.population.actions import PublicActions
from mockazure.population.catalog import get_adapter
from mockazure.population.identity import resolve_identity
from mockazure.population.transport import LocalTransport
from mockazure.population.types import AdapterContext, Mutation
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_connected_collaboration import populate
from tests.integration.test_teams_mutations import team_member
from tests.unit.test_business_relationships import values


async def test_named_delegated_sender_uses_real_local_login_and_readback(
    database, settings, monkeypatch
):
    async with graph_client(database, settings) as (client, tenant, app, _):
        _, data = await populate(database, settings, tenant)
        user = UUID(data["users"].items[0].id)
        ctx = AdapterContext(database, settings, tenant)
        identity = get_adapter("identity.clients")
        actor = await identity.apply(
            ctx,
            [
                Mutation(
                    operation="provision",
                    logical_key="sender",
                    payload={
                        "id": "sender",
                        "display_name": "Named sender",
                        "delegated_user_id": str(user),
                        "permissions": [
                            {
                                "resource": "graph",
                                "kind": "delegated",
                                "permissions": ["ChannelMessage.Send", "ChannelMessage.Read.All"],
                            }
                        ],
                    },
                )
            ],
        )
        assert "password" not in actor.model_dump_json() and "secret" not in actor.model_dump_json()
        team = data["teams"].items[0].id
        channel = values("channels", 6, tenant=str(tenant))[0].payload["payload"]["id"]
        admin = await headers_for(
            database, settings, tenant, app, None, ["TeamMember.ReadWrite.All"]
        )
        await team_member(client, tenant, admin, team, user, ["owner"])

        async def transport(self, context, name):
            return LocalTransport(context, await resolve_identity(context, name), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)
        adapter = get_adapter("teams.chatMessage")
        mutation = Mutation(
            operation="send-channel",
            logical_key="send",
            identity_ref="sender",
            payload={
                "variables": {"team": team, "channel": channel},
                "body": {"body": {"contentType": "text", "content": "Authenticated activity"}},
            },
        )
        assert not (await adapter.inspect(ctx, mutation)).exists
        result = await adapter.apply(ctx, [mutation])
        assert (await adapter.verify(ctx, result)).status == "succeeded"
        read = await headers_for(database, settings, tenant, app, None, ["ChannelMessage.Read.All"])
        message = await client.get(result.items[0].properties["read_path"], headers=read)
        assert message.json()["from"]["user"]["id"] == str(user)
        async with database.sessions.begin() as session:
            row = await session.get(User, user)
            row.enabled = False
        with pytest.raises(AdminError, match="login"):
            await adapter.apply(ctx, [mutation])


async def test_delegated_bootstrap_never_replaces_an_existing_user_password(database, settings):
    async with graph_client(database, settings) as (_, tenant, _, user):
        ctx = AdapterContext(database, settings, tenant)
        with pytest.raises(AdminError, match="credential"):
            await get_adapter("identity.clients").apply(
                ctx,
                [
                    Mutation(
                        operation="provision",
                        logical_key="must-not-reset",
                        payload={
                            "id": "must-not-reset",
                            "display_name": "No reset",
                            "delegated_user_id": str(user),
                            "permissions": [
                                {
                                    "resource": "graph",
                                    "kind": "delegated",
                                    "permissions": ["Chat.Create"],
                                }
                            ],
                        },
                    )
                ],
            )
