from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mockazure.identity.models import ServicePrincipal, User
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_arm_authorization import READ, actor, assign
from tests.integration.test_arm_state import seed_arm


@pytest.mark.parametrize("kind", ["application", "delegated"])
async def test_disabled_and_deleted_principals_do_not_keep_scoped_access(database, settings, kind):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, base, scope, resource = await seed_arm(client, tenant)
        from mockazure.providers.arm.authorization import require_action
        from mockazure.providers.arm.errors import ArmError
        from mockazure.providers.arm.scopes import parse_scope

        model = User if kind == "delegated" else ServicePrincipal
        async with database.sessions() as session:
            identifier = (
                user
                if kind == "delegated"
                else await session.scalar(
                    select(ServicePrincipal.id).where(
                        ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                    )
                )
            )
        await assign(
            client,
            base,
            identifier,
            scope,
            "Reader",
            "User" if kind == "delegated" else "ServicePrincipal",
        )
        context = actor(tenant, app, user if kind == "delegated" else None)
        for field, value in (("enabled", False), ("deleted_at", datetime.now(UTC))):
            async with database.sessions.begin() as session:
                row = await session.get(model, identifier)
                setattr(row, field, value)
            async with database.sessions() as session:
                with pytest.raises(ArmError) as error:
                    await require_action(session, context, parse_scope(resource["id"]), READ)
                assert error.value.status == 403
            async with database.sessions.begin() as session:
                row = await session.get(model, identifier)
                setattr(row, field, True if field == "enabled" else None)


async def test_foreign_principal_fk_and_legacy_grant_authority(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        subscription, base, scope, _ = await seed_arm(client, tenant)
        foreign = (
            await client.post(
                "/_mock/tenants",
                json={
                    "slug": "arm-foreign",
                    "display_name": "Foreign",
                    "domain": "foreign.test",
                    "licences": [],
                },
            )
        ).json()["id"]
        foreign_user = (
            await client.post(
                f"/_mock/tenants/{foreign}/identity/users",
                json={
                    "upn": "foreign@foreign.test",
                    "display_name": "Foreign",
                    "password": "local-foreign-password",
                },
            )
        ).json()["id"]
        response = await client.put(
            base + "/role-assignments/" + str(uuid4()),
            json={
                "principal_id": foreign_user,
                "principal_type": "User",
                "scope": scope,
                "role": "Owner",
            },
        )
        assert response.status_code == 404, response.text
        from mockazure.providers.arm.models import RoleAssignment

        with pytest.raises(IntegrityError):
            async with database.sessions.begin() as session:
                session.add(
                    RoleAssignment(
                        tenant_id=tenant,
                        subscription_id=UUID(subscription),
                        user_id=UUID(foreign_user),
                        scope_key=f"/subscriptions/{subscription}",
                    )
                )
                await session.flush()
        legacy = base + "/assignments/" + str(uuid4())
        assert (await client.put(legacy, json={"client_id": str(app)})).status_code == 200
        async with database.sessions() as session:
            principal = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        response = await client.put(
            base + "/role-assignments/" + str(uuid4()),
            json={
                "principal_id": str(principal),
                "principal_type": "ServicePrincipal",
                "scope": f"/subscriptions/{subscription}",
                "role": "Reader",
            },
        )
        assert response.status_code == 409, response.text
        assert (await client.delete(legacy)).status_code == 200
        headers = await headers_for(
            database, settings, tenant, app, None, set(), audience="https://management.azure.com/"
        )
        assert (await client.get("/subscriptions?api-version=2021-01-01", headers=headers)).json()[
            "value"
        ] == []
        await assign(client, base, principal, f"/subscriptions/{subscription}", "Reader")
        assert (
            await client.patch(
                f"/_mock/tenants/{tenant}/identity/applications/{app}", json={"enabled": False}
            )
        ).status_code == 200
        response = await client.get("/subscriptions?api-version=2021-01-01", headers=headers)
        assert response.status_code == 403, response.text
