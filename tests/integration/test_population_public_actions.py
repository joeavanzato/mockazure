from uuid import UUID

import pytest

from mockazure.admin.errors import AdminError
from mockazure.population.actions import PublicActions
from mockazure.population.catalog import get_adapter
from mockazure.population.identity import resolve_identity
from mockazure.population.transport import LocalTransport
from mockazure.population.types import AdapterContext, Mutation
from tests.integration.graph_support import graph_client
from tests.integration.test_graph_calendar_workflows import event_body
from tests.integration.test_mail_content import seed_mail


async def test_calendar_action_uses_declared_identity_and_captures_public_id(
    database, settings, monkeypatch
):
    async with graph_client(database, settings) as (client, tenant, _, owner):
        await seed_mail(client, database, tenant, owner)
        ctx = AdapterContext(database, settings, UUID(str(tenant)))
        await get_adapter("identity.clients").apply(
            ctx,
            [
                Mutation(
                    operation="provision",
                    logical_key="writer",
                    payload={
                        "id": "writer",
                        "display_name": "Calendar writer",
                        "permissions": [
                            {
                                "resource": "graph",
                                "kind": "application",
                                "permissions": ["Calendars.ReadWrite"],
                            }
                        ],
                    },
                )
            ],
        )

        async def transport(self, context, identity):
            return LocalTransport(context, await resolve_identity(context, identity), client=client)

        monkeypatch.setattr(PublicActions, "transport", transport)
        adapter = get_adapter("mail.events")
        mutation = Mutation(
            operation="create",
            logical_key="meeting",
            identity_ref="writer",
            payload={"variables": {"user": str(owner)}, "body": event_body()},
        )
        preview = await adapter.inspect(ctx, mutation)
        assert not preview.exists
        result = await adapter.apply(
            ctx, [mutation.model_copy(update={"expected_version": "absent"})]
        )
        assert result.items[0].id
        assert (await adapter.verify(ctx, result)).status == "succeeded"
        assert "body" not in result.model_dump_json()
        changed = await (await transport(adapter, ctx, "writer")).request(
            "graph",
            "PATCH",
            result.items[0].properties["read_path"],
            json={"subject": "Changed outside population"},
        )
        assert changed.status_code == 200
        assert (await adapter.verify(ctx, result)).status == "failed"

        denied = mutation.model_copy(update={"identity_ref": "missing"})
        with pytest.raises(AdminError):
            await adapter.apply(ctx, [denied])


def test_service_actions_reject_paths_and_unexpected_inputs():
    adapter = get_adapter("mail.events")
    for payload in (
        {"path": "https://graph.microsoft.com/v1.0/users"},
        {"variables": {"user": "../_mock"}, "body": {}},
        {"variables": {"user": "some-user"}, "body": {"madeUpGraphProperty": 1}},
    ):
        with pytest.raises(ValueError):
            adapter.validate(Mutation(operation="create", logical_key="bad", payload=payload))
