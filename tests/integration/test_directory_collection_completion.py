"""Missing collection paths must resolve canonical state and obey operation permissions."""

from uuid import uuid4

import pytest

from mockazure.directory.models import DirectoryRelation
from tests.integration.graph_support import (
    graph_client,
    headers_for,
    persisted_group,
    seed_relation_versions,
)


async def test_get_by_ids_mixed_visibility(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = str(group.id)
        response = await client.post(
            "/v1.0/directoryObjects/getByIds",
            headers=headers,
            json={"ids": [str(user), group_id, str(uuid4()), str(user)]},
        )
        assert response.status_code == 200, response.text
        values = {x["id"]: x for x in response.json()["value"]}
        alias = await client.post(
            "/v1.0/directoryObjects/microsoft.graph.getByIds",
            headers=headers,
            json={"ids": [str(user), group_id]},
        )
        assert alias.status_code == 200, alias.text
        assert alias.json()["value"] == response.json()["value"]
        assert len(response.json()["value"]) == 2
        assert values[str(user)]["@odata.type"] == "#microsoft.graph.user"
        assert values[str(user)]["displayName"] == "Directory Reader"
        assert values[group_id]["@odata.type"] == "#microsoft.graph.group"
        assert "verifier" not in response.text and "password" not in response.text.lower()
        selected = await client.post(
            "/v1.0/directoryObjects/getByIds",
            headers=headers,
            json={"ids": [str(user), group_id], "types": ["GROUP"]},
        )
        assert [x["id"] for x in selected.json()["value"]] == [group_id]
        item = await client.get(f"/v1.0/directoryObjects/{user}", headers=headers)
        assert item.status_code == 200, item.text
        assert item.json()["id"] == str(user)
        low = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        assert (
            await client.post(
                "/v1.0/directoryObjects/getByIds", headers=low, json={"ids": [str(user)]}
            )
        ).status_code == 403
        for body in (
            {"ids": [str(user)] * 1001},
            {"ids": ["not-guid"]},
            {"ids": str(user)},
            {"ids": [str(user)], "types": ["unknown"]},
        ):
            invalid = await client.post(
                "/v1.0/directoryObjects/getByIds", headers=headers, json=body
            )
            assert invalid.status_code == 400, invalid.text
        missing = await client.get(f"/v1.0/directoryObjects/{uuid4()}", headers=headers)
        assert missing.status_code == 404


async def test_relationship_cycles_and_paging(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            groups = [
                await persisted_group(session, tenant, name) for name in ("Leaf", "Middle", "Top")
            ]
            ids = [g.id for g in groups]
            for parent, child in ((ids[1], ids[0]), (ids[2], ids[1]), (ids[0], ids[2])):
                session.add(
                    DirectoryRelation(
                        tenant_id=tenant, group_id=parent, object_id=child, relation="member"
                    )
                )
        await seed_relation_versions(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, None, {"GroupMember.ReadBasic.All"}
        )
        direct = await client.get(f"/v1.0/groups/{ids[0]}/memberOf", headers=headers)
        assert direct.status_code == 200, direct.text
        assert [x["id"] for x in direct.json()["value"]] == [str(ids[1])]
        url = f"/v1.0/groups/{ids[0]}/transitiveMemberOf?$top=1"
        collected = []
        for _ in range(4):
            page = await client.get(url, headers=headers)
            assert page.status_code == 200, page.text
            collected.extend(x["id"] for x in page.json()["value"])
            url = page.json().get("@odata.nextLink")
            if not url:
                break
        assert url is None
        assert len(collected) == 2
        assert set(collected) == {str(ids[1]), str(ids[2])}
        wrong = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        assert (
            await client.get(f"/v1.0/groups/{ids[0]}/memberOf", headers=wrong)
        ).status_code == 403


async def test_me_alias_requires_delegated_identity(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=group.id, object_id=user, relation="member"
                )
            )
        await seed_relation_versions(database, tenant)
        headers = await headers_for(
            database, settings, tenant, app, user, {"User.Read"}, "delegated"
        )
        for relation in ("memberOf", "transitiveMemberOf"):
            response = await client.get(f"/v1.0/me/{relation}", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["value"][0]["id"] == str(group_id)
            assert response.json()["value"][0]["displayName"] is None
        application = await headers_for(
            database, settings, tenant, app, None, {"Directory.Read.All"}
        )
        assert (await client.get("/v1.0/me/memberOf", headers=application)).status_code == 400


async def test_owner_and_registered_user_reference_roundtrip(database, settings):
    from mockazure.identity.models import DirectoryRole

    async with graph_client(database, settings) as (client, tenant, app, user):
        admin = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"Directory.Read.All", "Group.ReadWrite.All", "Device.ReadWrite.All"},
        )
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
            session.add(DirectoryRole(tenant_id=tenant, user_id=user, role="Intune Administrator"))
        reference = {"@odata.id": settings.endpoints.graph + f"/v1.0/users/{user}"}
        created = await client.post(
            f"/v1.0/groups/{group_id}/owners/$ref", headers=admin, json=reference
        )
        assert created.status_code == 204, created.text
        owners = await client.get(f"/v1.0/groups/{group_id}/owners", headers=admin)
        assert [x["id"] for x in owners.json()["value"]] == [str(user)]
        owner_reader = await headers_for(
            database, settings, tenant, app, user, {"Directory.Read.All"}, "delegated"
        )
        owned = await client.get(f"/v1.0/users/{user}/ownedObjects?$top=1", headers=owner_reader)
        assert owned.status_code == 200, owned.text
        assert owned.json()["value"][0]["id"] == str(group_id)
        last = await client.delete(f"/v1.0/groups/{group_id}/owners/{user}/$ref", headers=admin)
        assert last.status_code == 400
        second = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "other@contoso.test",
                "display_name": "Other owner",
                "password": "local-other-password",
            },
        )
        assert second.status_code == 201
        added = await client.post(
            f"/v1.0/groups/{group_id}/owners/$ref",
            headers=admin,
            json={"@odata.id": settings.endpoints.graph + "/v1.0/users/" + second.json()["id"]},
        )
        assert added.status_code == 204
        removed = await client.delete(f"/v1.0/groups/{group_id}/owners/{user}/$ref", headers=admin)
        assert removed.status_code == 204, removed.text
        assert (await client.get(f"/v1.0/users/{user}/ownedObjects", headers=owner_reader)).json()[
            "value"
        ] == []
        delegated = await headers_for(
            database, settings, tenant, app, user, {"Directory.AccessAsUser.All"}, "delegated"
        )
        device = await client.post(
            "/v1.0/devices",
            headers=delegated,
            json={"displayName": "Evidence laptop", "deviceId": str(uuid4())},
        )
        assert device.status_code == 201, device.text
        device_id = device.json()["id"]
        route = f"/v1.0/devices/{device_id}/registeredUsers"
        assert (
            await client.post(route + "/$ref", headers=admin, json=reference)
        ).status_code == 403
        delegated = await headers_for(
            database, settings, tenant, app, user, {"Directory.AccessAsUser.All"}, "delegated"
        )
        created = await client.post(route + "/$ref", headers=delegated, json=reference)
        assert created.status_code == 204, created.text
        values = await client.get(route, headers=admin)
        assert values.status_code == 200, values.text
        assert [x["id"] for x in values.json()["value"]] == [str(user)]
        invalid = await client.post(
            route + "/$ref",
            headers=delegated,
            json={"@odata.id": settings.endpoints.graph + f"/v1.0/groups/{group_id}"},
        )
        assert invalid.status_code == 400
        removed = await client.delete(route + f"/{user}/$ref", headers=delegated)
        assert removed.status_code == 204, removed.text
        assert (await client.get(route, headers=admin)).json()["value"] == []


async def test_assignment_navigation_parity(database, settings):
    from sqlalchemy import select

    from mockazure.identity.models import ServicePrincipal

    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            resource = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
            group = await persisted_group(session, tenant)
            group_id = group.id
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=group_id, object_id=user, relation="member"
                )
            )
        assignments = [
            {
                "id": str(uuid4()),
                "principalId": str(principal),
                "resourceId": str(resource),
                "appRoleId": "00000000-0000-0000-0000-000000000000",
            }
            for principal in (user, group_id)
        ]
        imported = await client.put(
            f"/_mock/tenants/{tenant}/directory/collectionState",
            json={"appRoleAssignments": assignments},
        )
        assert imported.status_code == 200, imported.text
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        user_values = await client.get(f"/v1.0/users/{user}/appRoleAssignments", headers=headers)
        assert user_values.status_code == 200, user_values.text
        assert {x["id"] for x in user_values.json()["value"]} == {x["id"] for x in assignments}
        group_values = await client.get(
            f"/v1.0/groups/{group_id}/appRoleAssignments", headers=headers
        )
        assert [x["id"] for x in group_values.json()["value"]] == [assignments[1]["id"]]
        reverse = await client.get(
            f"/v1.0/servicePrincipals/{resource}/appRoleAssignedTo", headers=headers
        )
        assert {x["id"] for x in reverse.json()["value"]} == {x["id"] for x in assignments}
        denied = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        assert (
            await client.get(f"/v1.0/users/{user}/appRoleAssignments", headers=denied)
        ).status_code == 403
        exported = await client.get(f"/_mock/tenants/{tenant}/directory/collectionState")
        assert {x["id"] for x in exported.json()["appRoleAssignments"]} == {
            x["id"] for x in assignments
        }
        removed = await client.put(
            f"/_mock/tenants/{tenant}/directory/collectionState", json={"appRoleAssignments": []}
        )
        assert removed.status_code == 200
        assert (await client.get(f"/v1.0/users/{user}/appRoleAssignments", headers=headers)).json()[
            "value"
        ] == []


async def test_organization_role_fields_roundtrip(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        profile = {
            "city": "Boston",
            "country": "US",
            "technicalNotificationMails": ["ops@contoso.test"],
        }
        imported = await client.put(
            f"/_mock/tenants/{tenant}/directory/collectionState", json={"organization": profile}
        )
        assert imported.status_code == 200, imported.text
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"Organization.Read.All", "RoleManagement.Read.Directory"},
        )
        response = await client.get(
            "/v1.0/organization?$select=id,city,country,technicalNotificationMails", headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["value"] == [{"id": str(tenant), **profile}]
        role = await client.get(
            "/v1.0/roleManagement/directory/roleDefinitions/fe930be7-5e62-47db-91af-98c3a49a38b1?$select=id,description,rolePermissions",
            headers=headers,
        )
        assert role.status_code == 200, role.text
        assert isinstance(role.json()["description"], str) and role.json()["description"]
        assert role.json()["rolePermissions"]
        invalid = await client.put(
            f"/_mock/tenants/{tenant}/directory/collectionState",
            json={"organization": {"city": 42}},
        )
        assert invalid.status_code in (400, 422)
        unchanged = await client.get("/v1.0/organization?$select=city", headers=headers)
        assert unchanged.json()["value"] == [{"city": "Boston"}]


async def test_collection_state_normalization_and_atomic_rejection(database, settings):
    from sqlalchemy import select

    from mockazure.identity.models import ServicePrincipal

    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions() as session:
            resource = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        path = f"/_mock/tenants/{tenant}/directory/collectionState"
        assignment = {
            "id": str(uuid4()).upper(),
            "principalId": str(user).upper(),
            "resourceId": str(resource).upper(),
            "appRoleId": "00000000-0000-0000-0000-000000000000",
        }
        response = await client.put(path, json={"appRoleAssignments": [assignment]})
        assert response.status_code == 200, response.text
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        found = await client.get(f"/v1.0/users/{user}/appRoleAssignments", headers=headers)
        assert [x["id"] for x in found.json()["value"]] == [assignment["id"].lower()]
        state = response.json()
        for malformed in (False, 17, [], {}):
            failed = await client.put(
                path,
                json={
                    "organization": {"city": "Must roll back"},
                    "registeredUsers": [{"deviceId": malformed, "userId": str(user)}],
                },
            )
            assert failed.status_code == 400, failed.text
            assert (await client.get(path)).json() == state
        assert (await client.put(path, json={"appRoleAssignments": []})).status_code == 200
        assert (await client.put(path, json=state)).json() == state


async def test_organization_full_fields_require_role_with_mixed_scopes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await client.put(
            f"/_mock/tenants/{tenant}/directory/collectionState",
            json={"organization": {"city": "Boston"}},
        )
        basic = await headers_for(database, settings, tenant, app, user, {"User.Read"}, "delegated")
        response = await client.get("/v1.0/organization?$select=city", headers=basic)
        assert response.status_code == 200
        assert response.json()["value"] == [{"city": None}]
        mixed = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"User.Read", "Organization.Read.All"},
            "delegated",
        )
        assert (await client.get("/v1.0/organization", headers=mixed)).status_code == 403
        write = await headers_for(
            database, settings, tenant, app, None, {"Organization.ReadWrite.All"}
        )
        response = await client.get("/v1.0/organization?$select=city", headers=write)
        assert response.status_code == 200, response.text
        assert response.json()["value"] == [{"city": "Boston"}]


async def test_group_assignment_higher_permission(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
        headers = await headers_for(
            database, settings, tenant, app, None, {"Directory.ReadWrite.All"}
        )
        response = await client.get(f"/v1.0/groups/{group_id}/appRoleAssignments", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["value"] == []


async def test_registered_user_read_permission_is_operation_specific(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(
            database, settings, tenant, app, user, {"Device.ReadWrite.All"}, "delegated"
        )
        response = await client.get(f"/v1.0/devices/{uuid4()}/registeredUsers", headers=headers)
        assert response.status_code == 403, response.text


async def test_generic_resolution_all_types_excludes_foreign_and_deleted(database, settings):
    from datetime import UTC, datetime

    from sqlalchemy import select

    from mockazure.db.models import Tenant
    from mockazure.identity.models import Application, ServicePrincipal, User
    from mockazure.security.models import SecurityObject

    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            application = await session.get(Application, app)
            principal = await session.scalar(
                select(ServicePrincipal).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
            device_id, device_key = uuid4(), uuid4()
            session.add(
                SecurityObject(
                    tenant_id=tenant,
                    id=device_id,
                    kind="device",
                    properties={"displayName": "Local device", "deviceId": str(device_key)},
                )
            )
            foreign_tenant = Tenant(
                slug="foreign", display_name="Foreign", domain="foreign.test", licences=[]
            )
            session.add(foreign_tenant)
            await session.flush()
            foreign = User(
                tenant_id=foreign_tenant.id,
                upn="u@foreign.test",
                display_name="Foreign",
                verifier="disabled-test-fixture",
            )
            deleted = User(
                tenant_id=tenant,
                upn="deleted@contoso.test",
                display_name="Deleted",
                deleted_at=datetime.now(UTC),
                verifier="disabled-test-fixture",
            )
            session.add_all([foreign, deleted])
            await session.flush()
            expected = {
                str(user): "user",
                str(group.id): "group",
                str(application.object_id): "application",
                str(principal.id): "servicePrincipal",
                str(device_id): "device",
            }
            absent = [str(foreign.id), str(deleted.id)]
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        response = await client.post(
            "/v1.0/directoryObjects/getByIds",
            headers=headers,
            json={"ids": list(expected) + absent},
        )
        assert response.status_code == 200, response.text
        assert {
            x["id"]: x["@odata.type"].removeprefix("#microsoft.graph.")
            for x in response.json()["value"]
        } == expected
        for identifier in absent:
            assert (
                await client.get("/v1.0/directoryObjects/" + identifier, headers=headers)
            ).status_code == 404
        for value in response.json()["value"]:
            assert "secretText" not in str(value) and "password_hash" not in str(value)


async def test_owned_object_snapshot_resume_and_disclosure(database, settings):
    import httpx
    from sqlalchemy import delete

    from mockazure.admin.app import create_app

    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            groups = [
                await persisted_group(session, tenant, name) for name in ("One", "Two", "Three")
            ]
            ids = {str(group.id) for group in groups}
            session.add_all(
                [
                    DirectoryRelation(
                        tenant_id=tenant, group_id=group.id, object_id=user, relation="owner"
                    )
                    for group in groups
                ]
            )
        basic = await headers_for(database, settings, tenant, app, user, {"User.Read"}, "delegated")
        first = await client.get(
            "/v1.0/me/ownedObjects?$top=1&$select=id,displayName", headers=basic
        )
        assert first.status_code == 200, first.text
        assert first.json()["value"][0]["displayName"] is None
        assert first.json()["value"][0]["@odata.type"] == "#microsoft.graph.group"
        assert first.json()["@odata.context"].endswith("#directoryObjects")
        next_link = first.json()["@odata.nextLink"]
        async with database.sessions.begin() as session:
            await session.execute(
                delete(DirectoryRelation).where(DirectoryRelation.tenant_id == tenant)
            )
        seen = {first.json()["value"][0]["id"]}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as second:
            while next_link:
                page = await second.get(next_link, headers=basic)
                assert page.status_code == 200, page.text
                seen.update(x["id"] for x in page.json()["value"])
                next_link = page.json().get("@odata.nextLink")
        assert seen == ids
        assert (await client.get("/v1.0/me/ownedObjects", headers=basic)).json()["value"] == []


async def test_collection_migration_preserves_038_data_and_refuses_lossy_downgrade(settings):
    import pytest
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration, migrate

    url = settings.database_url.get_secret_value()
    engine = create_engine(url, hide_parameters=True)
    tenant, user, device = uuid4(), uuid4(), uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            command.upgrade(config, "038")
            connection.execute(
                text(
                    "INSERT INTO tenants (id,slug,display_name,domain,licences,revision) "
                    "VALUES (:id,'migration','Migration','migration.test','[]',1)"
                ),
                {"id": tenant},
            )
            connection.execute(
                text(
                    "INSERT INTO identity_users "
                    "(id,tenant_id,upn,display_name,verifier,enabled,profile) "
                    "VALUES (:id,:tenant,'user@migration.test','User','disabled',true,'{}')"
                ),
                {"id": user, "tenant": tenant},
            )
            connection.execute(
                text(
                    "INSERT INTO security_objects (tenant_id,id,kind,properties) "
                    "VALUES (:tenant,:id,'device','{}')"
                ),
                {"tenant": tenant, "id": device},
            )
        migrate(url)
        with engine.begin() as connection:
            assert (
                connection.scalar(
                    text("SELECT display_name FROM tenants WHERE id=:id"), {"id": tenant}
                )
                == "Migration"
            )
            assert (
                connection.scalar(
                    text("SELECT verifier FROM identity_users WHERE id=:id"), {"id": user}
                )
                == "disabled"
            )
            connection.execute(
                text("INSERT INTO security_device_users VALUES (:tenant,:device,:user)"),
                {"tenant": tenant, "device": device, "user": user},
            )
        with pytest.raises(DBAPIError, match="Export and remove collection state"):
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "038")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM security_device_users")) == 1
    finally:
        engine.dispose()


async def test_owned_objects_rejects_application_and_unsupported_self_scope(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        application = await headers_for(
            database, settings, tenant, app, None, {"Directory.Read.All"}
        )
        response = await client.get(f"/v1.0/users/{user}/ownedObjects", headers=application)
        assert response.status_code == 403, response.text
        write_self = await headers_for(
            database, settings, tenant, app, user, {"User.ReadWrite"}, "delegated"
        )
        assert (await client.get("/v1.0/me/ownedObjects", headers=write_self)).status_code == 403


async def test_service_principal_owner_audit_preserves_target_without_granting_consent(
    database, settings
):
    from sqlalchemy import func, select

    from mockazure.directory.models import EvidenceOutbox
    from mockazure.identity.models import Grant, ServicePrincipal

    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
            consent_before = await session.scalar(select(func.count()).select_from(Grant))
        headers = await headers_for(database, settings, tenant, app, None, {"Group.ReadWrite.All"})
        response = await client.post(
            f"/v1.0/groups/{group_id}/owners/$ref",
            headers=headers,
            json={"@odata.id": settings.endpoints.graph + f"/v1.0/servicePrincipals/{sp}"},
        )
        assert response.status_code == 204, response.text
        async with database.sessions() as session:
            facts = await session.scalar(
                select(EvidenceOutbox.facts).where(
                    EvidenceOutbox.tenant_id == tenant,
                    EvidenceOutbox.object_id == group_id,
                    EvidenceOutbox.facts["operation"].as_string() == "add_owner",
                )
            )
            assert any(
                x["id"] == str(sp) and x["type"] == "ServicePrincipal" for x in facts["targets"]
            )
            assert await session.scalar(select(func.count()).select_from(Grant)) == consent_before


async def test_review_registered_users_requires_delegated_directory_role(database, settings):
    from sqlalchemy import delete

    from mockazure.identity.models import DirectoryRole
    from mockazure.security.models import SecurityObject

    async with graph_client(database, settings) as (client, tenant, app, user):
        device = uuid4()
        async with database.sessions.begin() as session:
            session.add(SecurityObject(tenant_id=tenant, id=device, kind="device", properties={}))
        headers = await headers_for(
            database, settings, tenant, app, user, {"Device.Read.All"}, "delegated"
        )
        route = f"/v1.0/devices/{device}/registeredUsers"
        assert (await client.get(route, headers=headers)).status_code == 403
        for role in (
            "Directory Readers",
            "Global Reader",
            "Intune Administrator",
            "Windows 365 Administrator",
            "Global Administrator",
        ):
            async with database.sessions.begin() as session:
                session.add(DirectoryRole(tenant_id=tenant, user_id=user, role=role))
            assert (await client.get(route, headers=headers)).status_code == 200
            async with database.sessions.begin() as session:
                await session.execute(
                    delete(DirectoryRole).where(
                        DirectoryRole.tenant_id == tenant, DirectoryRole.user_id == user
                    )
                )
        assert (await client.get(route, headers=headers)).status_code == 403


async def test_review_intune_can_manage_service_principal_group_owners(database, settings):
    from sqlalchemy import select

    from mockazure.identity.models import DirectoryRole, ServicePrincipal

    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
            sp = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
            session.add(DirectoryRole(tenant_id=tenant, user_id=user, role="Intune Administrator"))
        headers = await headers_for(
            database, settings, tenant, app, user, {"Group.ReadWrite.All"}, "delegated"
        )
        path = f"/v1.0/groups/{group_id}/owners"
        added = await client.post(
            path + "/$ref",
            headers=headers,
            json={"@odata.id": settings.endpoints.graph + f"/v1.0/servicePrincipals/{sp}"},
        )
        assert added.status_code == 204, added.text
        removed = await client.delete(path + f"/{sp}/$ref", headers=headers)
        assert removed.status_code == 204, removed.text


async def test_review_assignment_null_timestamp_is_preserved(database, settings):
    from sqlalchemy import select

    from mockazure.identity.models import ServicePrincipal

    async with graph_client(database, settings) as (client, tenant, app, user):
        async with database.sessions() as session:
            resource = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == app
                )
            )
        assignment = {
            "id": str(uuid4()),
            "principalId": str(user),
            "resourceId": str(resource),
            "appRoleId": str(uuid4()),
            "createdDateTime": None,
        }
        assignment["appRoleId"] = "00000000-0000-0000-0000-000000000000"
        path = f"/_mock/tenants/{tenant}/directory/collectionState"
        imported = await client.put(path, json={"appRoleAssignments": [assignment]})
        assert imported.status_code == 200, imported.text
        assert imported.json()["appRoleAssignments"][0]["createdDateTime"] is None
        exported = (await client.get(path)).json()
        assert (await client.put(path, json=exported)).json() == exported
        headers = await headers_for(database, settings, tenant, app, None, {"Directory.Read.All"})
        graph = await client.get(f"/v1.0/users/{user}/appRoleAssignments", headers=headers)
        assert graph.json()["value"][0]["createdDateTime"] is None


@pytest.mark.parametrize(
    "deleted_kind", ["user", "group", "servicePrincipal", "application", "device"]
)
async def test_review_deletion_keeps_collection_state_roundtrippable(
    database, settings, deleted_kind
):
    from sqlalchemy import select

    from mockazure.identity.models import Application, ServicePrincipal
    from mockazure.security.models import SecurityObject
    from tests.integration.graph_support import registered_app

    async with graph_client(database, settings) as (client, tenant, app, user):
        from uuid import UUID

        resource_app = UUID(await registered_app(client, tenant))
        device = uuid4()
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
            resource = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant, ServicePrincipal.client_id == resource_app
                )
            )
            application = await session.get(Application, resource_app)
            application_id = application.object_id
            session.add(SecurityObject(tenant_id=tenant, id=device, kind="device", properties={}))
        path = f"/_mock/tenants/{tenant}/directory/collectionState"
        assignments = [
            {
                "id": str(uuid4()),
                "principalId": str(principal),
                "resourceId": str(resource),
                "appRoleId": "00000000-0000-0000-0000-000000000000",
            }
            for principal in (user, group_id)
        ]
        imported = await client.put(
            path,
            json={
                "appRoleAssignments": assignments,
                "registeredUsers": [{"deviceId": str(device), "userId": str(user)}],
            },
        )
        assert imported.status_code == 200, imported.text
        headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {
                "User.ReadWrite.All",
                "Group.ReadWrite.All",
                "Application.ReadWrite.All",
                "Device.ReadWrite.All",
                "Directory.Read.All",
            },
        )
        routes = {
            "user": f"users/{user}",
            "group": f"groups/{group_id}",
            "servicePrincipal": f"servicePrincipals/{resource}",
            "application": f"applications/{application_id}",
            "device": f"devices/{device}",
        }
        deleted = await client.delete("/v1.0/" + routes[deleted_kind], headers=headers)
        assert deleted.status_code == 204, deleted.text
        exported = (await client.get(path)).json()
        restored = await client.put(path, json=exported)
        assert restored.status_code == 200, restored.text
        assert restored.json() == exported
        expected_assignments = (
            2 if deleted_kind == "device" else 1 if deleted_kind in {"user", "group"} else 0
        )
        assert len(exported["appRoleAssignments"]) == expected_assignments
        assert len(exported["registeredUsers"]) == (0 if deleted_kind in {"user", "device"} else 1)
