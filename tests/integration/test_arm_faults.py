import pytest
from sqlalchemy import select

from mockazure.identity.models import ServicePrincipal
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm


@pytest.mark.parametrize(
    ("kind", "status", "code"),
    [
        ("next_read_429", 429, "TooManyRequests"),
        ("next_read_503", 503, "ServiceUnavailable"),
    ],
)
async def test_arm_activity_fault_is_one_shot_scoped_and_authorized(
    database, settings, kind, status, code
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        admin = base + "/activity-faults"
        fault = {"client_id": str(app), "kind": kind, "retry_after": 1}
        assert (await client.post(admin, json=fault, headers=headers)).status_code == 401
        assert (await client.post(admin, json=fault)).status_code == 201
        assert (await client.post(admin, json=fault)).status_code == 409
        path = f"/subscriptions/{sub}/providers/Microsoft.Insights/eventtypes/management/values"
        args = {"api-version": "2015-04-01", "$filter": "eventTimestamp ge '2026-10-01T00:00:00Z'"}
        # A fault must not mask missing scope authorization.
        assert (await client.get(path, params=args, headers=headers)).status_code == 403
        await assign(client, base, sp, f"/subscriptions/{sub}", "Reader")
        limited = await client.get(path, params=args, headers=headers)
        assert limited.status_code == status
        assert limited.headers["Retry-After"] == "1"
        assert limited.json()["error"]["code"] == code
        assert (await client.get(path, params=args, headers=headers)).status_code == 200
        assert (
            await client.post(admin, json={**fault, "kind": "next_page_429"})
        ).status_code == 201
        assert (await client.get(path, params=args, headers=headers)).status_code == 200
