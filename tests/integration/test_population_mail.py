from uuid import UUID

from mockazure.population.catalog import get_adapter
from mockazure.population.types import AdapterContext, Mutation, ResourceQuery
from tests.integration.graph_support import graph_client
from tests.integration.test_mail_content import seed_mail


async def test_mail_append_preserves_existing_content_and_workflows(
    database, settings, monkeypatch
):
    from mockazure.mail import workflow_lifecycle

    async with graph_client(database, settings) as (client, tenant, _, user):
        await seed_mail(client, database, tenant, user)
        ctx = AdapterContext(database, settings, UUID(str(tenant)))
        adapter = get_adapter("mail.messages")
        before = await adapter.browse(ctx, ResourceQuery(parent=str(user)))

        async def forbidden(*args, **kwargs):
            raise AssertionError("Appending evidence must not retire mailbox workflows")

        monkeypatch.setattr(workflow_lifecycle, "retire_mail_workflows", forbidden)
        mutation = Mutation(
            operation="append",
            logical_key="appended",
            parent=str(user),
            payload={
                "id": "appended",
                "folder_id": "inbox",
                "payload": {"subject": "Additional evidence", "isRead": False},
            },
        )
        result = await adapter.apply(ctx, [mutation])
        assert result.effects["created"] == 1
        assert (await adapter.apply(ctx, [mutation])).effects["unchanged"] == 1
        after = await adapter.browse(ctx, ResourceQuery(parent=str(user)))
        assert len(after.items) == len(before.items) + 1
        assert {r.id: r.version for r in before.items}.items() <= {
            r.id: r.version for r in after.items
        }.items()
