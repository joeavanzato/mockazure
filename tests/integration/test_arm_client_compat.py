from sqlalchemy import select

from mockazure.identity.models import ServicePrincipal
from tests.integration.graph_support import graph_client, registered_app
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm


async def test_group_head_matches_pinned_azure_cmdlet_contract(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Reader")
        args = {"api-version": "2021-04-01"}
        found = await client.head(group, params=args, headers=headers)
        assert found.status_code == 204 and not found.content
        assert (
            await client.head(group + "-absent", params=args, headers=headers)
        ).status_code == 404
        denied = await registered_app(client, tenant)
        denied_headers = await access(database, settings, tenant, denied)
        for path in (group, group + "-absent"):
            assert (await client.head(path, params=args, headers=denied_headers)).status_code == 403
        assert (
            await client.head(group, params={"api-version": "unknown"}, headers=headers)
        ).status_code == 400
