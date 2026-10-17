from uuid import uuid4

import pytest
from sqlalchemy import select

from mockazure.identity.models import ServicePrincipal
from mockazure.identity.types import PrincipalContext
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_state import seed_arm

READ = "Microsoft.Compute/virtualMachines/read"
WRITE = "Microsoft.Resources/tags/write"
ASSIGN = "Microsoft.Authorization/roleAssignments/write"


def actor(tenant, app, user=None):
    return PrincipalContext(
        tenant,
        app,
        user,
        "delegated" if user else "application",
        "https://management.azure.com/",
        frozenset({"user_impersonation"}) if user else frozenset(),
    )


async def assign(client, base, principal, scope, role, kind="ServicePrincipal", identifier=None):
    response = await client.put(
        base + "/role-assignments/" + str(identifier or uuid4()),
        json={"principal_id": str(principal), "principal_type": kind, "scope": scope, "role": role},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "role,write,rbac",
    [
        ("Reader", False, False),
        ("Contributor", True, False),
        ("Owner", True, True),
        ("User Access Administrator", False, True),
    ],
)
async def test_scoped_roles_and_permission_union(database, settings, role, write, rbac):
    async with graph_client(database, settings) as (client, tenant, app, user):
        subscription, base, scope, resource = await seed_arm(client, tenant)
        from mockazure.providers.arm.authorization import require_action
        from mockazure.providers.arm.errors import ArmError
        from mockazure.providers.arm.scopes import parse_scope

        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
            principal = sp.id
        # Public SP object ID is mandatory; the application client ID is not that identity.
        response = await client.put(
            base + "/role-assignments/" + str(uuid4()),
            json={
                "principal_id": str(app),
                "principal_type": "ServicePrincipal",
                "scope": scope,
                "role": role,
            },
        )
        assert response.status_code == 404, response.text
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Global Administrator"
            )
        ).status_code == 200
        async with database.sessions() as session:
            with pytest.raises(ArmError):
                await require_action(session, actor(tenant, app), parse_scope(resource["id"]), READ)
        grant = await assign(client, base, principal, scope, role)
        async with database.sessions() as session:
            for action, allowed in ((READ, True), (WRITE, write), (ASSIGN, rbac)):
                if allowed:
                    await require_action(
                        session, actor(tenant, app), parse_scope(resource["id"]), action
                    )
                else:
                    with pytest.raises(ArmError) as denied:
                        await require_action(
                            session, actor(tenant, app), parse_scope(resource["id"]), action
                        )
                    assert denied.value.status == 403
            for other in (f"/subscriptions/{subscription}", scope + "other"):
                with pytest.raises(ArmError):
                    await require_action(session, actor(tenant, app), parse_scope(other), READ)
        extra = None
        if role == "Contributor":
            extra = await assign(client, base, principal, scope, "User Access Administrator")
            async with database.sessions() as session:
                await require_action(
                    session, actor(tenant, app), parse_scope(resource["id"]), ASSIGN
                )
        assert (
            await client.patch(
                f"/_mock/tenants/{tenant}/identity/applications/{app}", json={"enabled": False}
            )
        ).status_code == 200
        async with database.sessions() as session:
            with pytest.raises(ArmError):
                await require_action(session, actor(tenant, app), parse_scope(resource["id"]), READ)
        assert (
            await client.patch(
                f"/_mock/tenants/{tenant}/identity/applications/{app}", json={"enabled": True}
            )
        ).status_code == 200
        assert (await client.delete(base + "/role-assignments/" + grant["name"])).status_code == 200

        if extra:
            assert (
                await client.delete(base + "/role-assignments/" + extra["name"])
            ).status_code == 200
        async with database.sessions() as session:
            with pytest.raises(ArmError):
                await require_action(session, actor(tenant, app), parse_scope(resource["id"]), READ)


async def test_current_transitive_group_membership_and_sql_visibility(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        _, base, scope, resource = await seed_arm(client, tenant)
        from mockazure.directory.models import DirectoryEntry, DirectoryRelation, Group
        from mockazure.providers.arm.authorization import readable_scopes, require_action
        from mockazure.providers.arm.errors import ArmError
        from mockazure.providers.arm.models import ArmResource
        from mockazure.providers.arm.scopes import parse_scope

        inner, outer = uuid4(), uuid4()
        async with database.sessions.begin() as session:
            for identifier in (inner, outer):
                session.add(
                    Group(
                        id=identifier,
                        tenant_id=tenant,
                        display_name="Security",
                        mail_nickname=identifier.hex,
                    )
                )
            await session.flush()
            for identifier in (inner, outer):
                session.add(
                    DirectoryEntry(
                        tenant_id=tenant, id=identifier, kind="group", group_id=identifier
                    )
                )
            await session.flush()
            for group, member in ((inner, user), (outer, inner), (inner, outer)):
                session.add(
                    DirectoryRelation(
                        tenant_id=tenant, group_id=group, object_id=member, relation="member"
                    )
                )
        await assign(client, base, outer, scope, "Reader", kind="Group")
        async with database.sessions() as session:
            context = actor(tenant, app, user)
            await require_action(session, context, parse_scope(resource["id"]), READ)
            rows = list(
                await session.scalars(
                    select(ArmResource).where(
                        ArmResource.tenant_id == tenant,
                        ArmResource.kind == "resource",
                        await readable_scopes(session, context, READ),
                    )
                )
            )
            assert [r.scope_key for r in rows] == [resource["id"].lower()]
        from datetime import UTC, datetime

        async with database.sessions.begin() as session:
            group = await session.get(Group, inner)
            group.deleted_at = datetime.now(UTC)
        async with database.sessions() as session:
            with pytest.raises(ArmError):
                await require_action(
                    session, actor(tenant, app, user), parse_scope(resource["id"]), READ
                )
        async with database.sessions.begin() as session:
            group = await session.get(Group, inner)
            group.deleted_at = None
        async with database.sessions.begin() as session:
            edge = await session.get(DirectoryRelation, (tenant, inner, user, "member"))
            await session.delete(edge)
        async with database.sessions() as session:
            with pytest.raises(ArmError):
                await require_action(
                    session, actor(tenant, app, user), parse_scope(resource["id"]), READ
                )
