import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from mockazure.evidence.models import EvidenceSource
from mockazure.identity.models import ServicePrincipal
from mockazure.providers.arm.contracts import builtin_roles, validate_arm_payload
from tests.integration.graph_support import graph_client
from tests.integration.test_arm_authorization import assign
from tests.integration.test_arm_inventory import access
from tests.integration.test_arm_state import seed_arm


async def test_public_arm_roles_assignments_and_legacy_revocation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Owner")
        scope = f"/subscriptions/{sub}"
        prefix = scope + "/providers/Microsoft.Authorization"
        args = {"api-version": "2022-04-01"}
        definitions = await client.get(prefix + "/roleDefinitions", params=args, headers=headers)
        assert definitions.status_code == 200, definitions.text
        assert {r["properties"]["roleName"] for r in definitions.json()["value"]} == set(
            builtin_roles()
        )
        for definition in definitions.json()["value"]:
            validate_arm_payload("RoleDefinition", definition, namespace="definitions")
            result = await client.get(definition["id"], params=args, headers=headers)
            assert result.status_code == 200 and result.json() == definition
        reader_id = prefix + "/roleDefinitions/" + builtin_roles()["Reader"]["id"]
        identifier = uuid4()
        path = group + "/providers/Microsoft.Authorization/roleAssignments/" + str(identifier)
        body = {"properties": {"principalId": str(user), "roleDefinitionId": reader_id}}
        created = await client.put(path, params=args, headers=headers, json=body)
        assert created.status_code == 201, created.text
        validate_arm_payload("RoleAssignment", created.json())
        assert created.json()["properties"]["principalType"] == "User"
        assert created.json()["properties"]["scope"] == group.lower()
        same = await client.put(path, params=args, headers=headers, json=body)
        assert same.status_code == 200 and same.json() == created.json()
        async with database.sessions() as session:
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "arm"
                    )
                )
            )
            assert len(facts) == 1
        changed = {"properties": body["properties"] | {"principalId": str(sp)}}
        assert (
            await client.put(path, params=args, headers=headers, json=changed)
        ).status_code == 409
        wrongscope = scope + "/providers/Microsoft.Authorization/roleAssignments/" + str(identifier)
        assert (await client.get(wrongscope, params=args, headers=headers)).status_code == 404
        assert (await client.delete(wrongscope, params=args, headers=headers)).status_code == 404
        collection = group + "/providers/Microsoft.Authorization/roleAssignments"
        listed = await client.get(
            collection, params=args | {"$filter": "atScope()"}, headers=headers
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["value"]) == 2  # group Reader plus inherited Owner
        listed = await client.get(
            prefix + "/roleAssignments",
            params=args | {"$filter": f"principalId eq '{user}'"},
            headers=headers,
        )
        assert listed.status_code == 200 and len(listed.json()["value"]) == 1
        for extra in (
            {"condition": "arbitrary"},
            {"delegatedManagedIdentityResourceId": resource["id"]},
            {"principalType": "Device"},
            {"principalId": str(uuid4())},
            {"roleDefinitionId": reader_id.replace(sub, str(uuid4()))},
            {
                "roleDefinitionId": reader_id.replace(
                    "Microsoft.Authorization", "MicrosoftXAuthorization"
                )
            },
        ):
            denied = await client.put(
                collection + "/" + str(uuid4()),
                params=args,
                headers=headers,
                json={"properties": body["properties"] | extra},
            )
            assert denied.status_code in {400, 404}, denied.text
        assert (
            await client.put(reader_id, params=args, headers=headers, json={"properties": {}})
        ).status_code in {405, 501}
        # Public assignment grants effective access; the legacy admin endpoint revokes the same row.
        user_headers = await access_delegated(database, settings, tenant, app, user)
        assert (
            await client.get(group, params={"api-version": "2021-04-01"}, headers=user_headers)
        ).status_code == 200
        assert (await client.delete(base + "/assignments/" + str(identifier))).status_code == 200
        assert (
            await client.get(group, params={"api-version": "2021-04-01"}, headers=user_headers)
        ).status_code == 403

        # Concurrent equivalent assignments yield one grant and one conflict.
        async def put():
            return await client.put(
                collection + "/" + str(uuid4()), params=args, headers=headers, json=body
            )

        results = await asyncio.gather(put(), put())
        assert sorted(r.status_code for r in results) == [201, 409]
        winner = next(r for r in results if r.status_code == 201)
        removed = await client.delete(winner.json()["id"], params=args, headers=headers)
        assert removed.status_code == 200, removed.text
        assert removed.json()["name"] == winner.json()["name"]
        assert (
            await client.delete(winner.json()["id"], params=args, headers=headers)
        ).status_code == 204


async def access_delegated(database, settings, tenant, app, user):
    from tests.integration.graph_support import headers_for

    return await headers_for(
        database,
        settings,
        tenant,
        app,
        user,
        {"user_impersonation"},
        kind="delegated",
        audience="https://management.azure.com/",
    )


async def test_contributor_cannot_grant_itself_owner(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, group, "Contributor")
        prefix = f"/subscriptions/{sub}/providers/Microsoft.Authorization"
        path = group + "/providers/Microsoft.Authorization/roleAssignments/" + str(uuid4())
        response = await client.put(
            path,
            params={"api-version": "2022-04-01"},
            headers=headers,
            json={
                "properties": {
                    "principalId": str(sp),
                    "principalType": "ServicePrincipal",
                    "roleDefinitionId": prefix
                    + "/roleDefinitions/"
                    + builtin_roles()["Owner"]["id"],
                }
            },
        )
        assert response.status_code == 403, response.text
        async with database.sessions() as session:
            assert not list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "arm"
                    )
                )
            )


@pytest.mark.parametrize("version", ["2022-04-01", "2020-08-01-preview"])
async def test_public_rbac_pages_and_operation_poll_recheck_revocation(database, settings, version):
    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, group, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        user_headers = await access_delegated(database, settings, tenant, app, user)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Owner")
        owner = await assign(client, base, user, group, "Owner", kind="User")
        resources = [
            resource | {"name": f"vm{i}", "id": resource["id"] + str(i)} for i in range(101)
        ]
        assert (
            await client.post(base + "/resources", json={"resources": resources})
        ).status_code == 200
        from mockazure.providers.arm.assignments import set_assignment
        from mockazure.providers.arm.scopes import parse_scope

        async with database.sessions.begin() as session:
            for resource_item in resources:
                await set_assignment(
                    session,
                    tenant,
                    UUID(sub),
                    uuid4(),
                    parse_scope(resource_item["id"]),
                    builtin_roles()["Reader"]["id"],
                    user,
                    "User",
                )
        collection = group + "/providers/Microsoft.Authorization/roleAssignments"
        args = {"api-version": version}
        for malformed in (f"principalId eq '{user}", f"principalId eq {user}'"):
            response = await client.get(
                collection, params=args | {"$filter": malformed}, headers=user_headers
            )
            assert response.status_code == 400, response.text
        first = await client.get(collection, params=args, headers=user_headers)
        assert first.status_code == 200, first.text
        assert len(first.json()["value"]) == 100
        link = first.json()["nextLink"]
        assert "%24skipToken=" in link
        assert "api-version=" + version in link
        other = "2020-08-01-preview" if version == "2022-04-01" else "2022-04-01"
        replay = await client.get(link.replace(version, other), headers=user_headers)
        assert replay.status_code == 400, replay.text
        second = await client.get(link, headers=user_headers)
        assert second.status_code == 200, second.text
        seen = first.json()["value"] + second.json()["value"]
        assert len(seen) == len({r["id"] for r in seen}) == 103
        for bad in ("principalId ne 'x'", "atScope() and anything", "unknown eq 'x'"):
            assert (
                await client.get(collection, params=args | {"$filter": bad}, headers=user_headers)
            ).status_code == 400
        deleting = await client.delete(
            group, params={"api-version": "2021-04-01"}, headers=user_headers
        )
        assert deleting.status_code == 202
        poll = deleting.headers["Location"]
        assert (await client.get(poll, headers=user_headers)).status_code == 202
        assert (
            await client.delete(owner["id"], params={"api-version": "2022-04-01"}, headers=headers)
        ).status_code == 200
        assert (await client.get(link, headers=user_headers)).status_code == 403
        assert (await client.get(poll, headers=user_headers)).status_code == 403


async def test_public_group_assignment_observes_transitive_edge_removal(database, settings):
    from mockazure.directory.models import DirectoryEntry, DirectoryRelation, Group

    async with graph_client(database, settings) as (client, tenant, app, user):
        sub, base, scope, resource = await seed_arm(client, tenant)
        headers = await access(database, settings, tenant, app)
        delegated = await access_delegated(database, settings, tenant, app, user)
        async with database.sessions() as session:
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        await assign(client, base, sp, f"/subscriptions/{sub}", "Owner")
        inner, outer = uuid4(), uuid4()
        async with database.sessions.begin() as session:
            for identifier in (inner, outer):
                session.add(
                    Group(
                        id=identifier,
                        tenant_id=tenant,
                        display_name="RBAC",
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
        prefix = f"/subscriptions/{sub}/providers/Microsoft.Authorization"
        collection = scope + "/providers/Microsoft.Authorization/roleAssignments"
        created = await client.put(
            collection + "/" + str(uuid4()),
            params={"api-version": "2022-04-01"},
            headers=headers,
            json={
                "properties": {
                    "principalId": str(outer),
                    "principalType": "Group",
                    "roleDefinitionId": prefix
                    + "/roleDefinitions/"
                    + builtin_roles()["Reader"]["id"],
                }
            },
        )
        assert created.status_code == 201, created.text
        assert (
            await client.get(collection, params={"api-version": "2022-04-01"}, headers=delegated)
        ).status_code == 200
        async with database.sessions.begin() as session:
            edge = await session.get(DirectoryRelation, (tenant, inner, user, "member"))
            await session.delete(edge)
        assert (
            await client.get(collection, params={"api-version": "2022-04-01"}, headers=delegated)
        ).status_code == 403
