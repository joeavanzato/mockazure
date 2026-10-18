"""Public Exchange RBAC changes current command authority without changing OAuth grants."""

from sqlalchemy import select

from mockazure.identity.models import Grant
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_exchange_mail import command
from tests.integration.test_exchange_security_configuration import setup


async def delegated(database, settings, tenant, app, user):
    return await headers_for(
        database,
        settings,
        tenant,
        app,
        user,
        {"Exchange.Manage"},
        kind="delegated",
        audience="https://outlook.office365.com",
    )


async def oauth_grants(database, tenant):
    async with database.sessions() as session:
        rows = list(await session.scalars(select(Grant).where(Grant.tenant_id == tenant)))
        return sorted(
            (str(r.id), str(r.client_id), r.resource, tuple(r.permissions), r.kind) for r in rows
        )


async def test_role_change_enforcement(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        admin = await setup(client, database, settings, tenant, app, user)
        actor = await delegated(database, settings, tenant, app, user)
        before = await oauth_grants(database, tenant)
        assert (
            await command(client, tenant, actor, "Get-Mailbox", {"Identity": str(user)})
        ).status_code == 403
        groups = await command(client, tenant, admin, "Get-RoleGroup", {})
        assert groups.status_code == 200, groups.text
        assert {
            "Organization Management",
            "Recipient Management",
            "View-Only Organization Management",
        } <= {r["Name"] for r in groups.json()["value"]}
        added = await command(
            client,
            tenant,
            admin,
            "Add-RoleGroupMember",
            {
                "Identity": "Recipient Management",
                "Member": str(user),
                "BypassSecurityGroupManagerCheck": True,
            },
        )
        assert added.status_code == 200, added.text
        members = await command(
            client, tenant, admin, "Get-RoleGroupMember", {"Identity": "Recipient Management"}
        )
        assert members.status_code == 200 and str(user) in {
            r["ExternalDirectoryObjectId"] for r in members.json()["value"]
        }, members.text
        assert (
            await command(
                client,
                tenant,
                actor,
                "Set-Mailbox",
                {"Identity": str(user), "ForwardingSmtpAddress": "rbac@example.test"},
            )
        ).status_code == 200
        assert (
            await command(
                client,
                tenant,
                actor,
                "Set-AdminAuditLogConfig",
                {"UnifiedAuditLogIngestionEnabled": False},
            )
        ).status_code == 403
        module = await client.get(
            f"/AdminApi/v1.0/{tenant}/EXOModuleFile?Version=3.11.0",
            headers=actor | {"CommandName": "Set-Mailbox"},
        )
        assert module.status_code == 200, module.text
        removed = await command(
            client,
            tenant,
            admin,
            "Remove-RoleGroupMember",
            {
                "Identity": "Recipient Management",
                "Member": str(user),
                "BypassSecurityGroupManagerCheck": True,
            },
        )
        assert removed.status_code == 200, removed.text
        assert (
            await command(
                client,
                tenant,
                actor,
                "Set-Mailbox",
                {"Identity": str(user), "ForwardingSmtpAddress": "revoked@example.test"},
            )
        ).status_code == 403
        assert (
            await client.get(
                f"/AdminApi/v1.0/{tenant}/EXOModuleFile?Version=3.11.0",
                headers=actor | {"CommandName": "Set-Mailbox"},
            )
        ).status_code == 403
        assert await oauth_grants(database, tenant) == before


async def test_management_role_assignment_revokes_existing_token(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        admin = await setup(client, database, settings, tenant, app, user)
        actor = await delegated(database, settings, tenant, app, user)
        assignment = await command(
            client,
            tenant,
            admin,
            "New-ManagementRoleAssignment",
            {
                "Name": "Local recipient",
                "Role": "Mail Recipients",
                "User": str(user),
                "RecipientRelativeWriteScope": "Organization",
            },
        )
        assert assignment.status_code == 200, assignment.text
        row = assignment.json()["value"][0]
        listed = await command(
            client, tenant, admin, "Get-ManagementRoleAssignment", {"Identity": row["Guid"]}
        )
        assert (
            listed.status_code == 200 and listed.json()["value"][0]["Name"] == "Local recipient"
        ), listed.text
        assert (
            await command(
                client,
                tenant,
                actor,
                "Set-Mailbox",
                {"Identity": str(user), "ForwardingSmtpAddress": "direct@example.test"},
            )
        ).status_code == 200
        assert (
            await command(
                client,
                tenant,
                actor,
                "Add-RoleGroupMember",
                {"Identity": "Organization Management", "Member": str(user)},
            )
        ).status_code == 403
        assert (
            await command(
                client, tenant, admin, "Remove-ManagementRoleAssignment", {"Identity": row["Guid"]}
            )
        ).status_code == 200
        assert (
            await command(
                client,
                tenant,
                actor,
                "Set-Mailbox",
                {"Identity": str(user), "ForwardingSmtpAddress": "revoked@example.test"},
            )
        ).status_code == 403
        for bad in (
            {"Role": "Invented", "User": str(user)},
            {"Role": "Mail Recipients", "User": str(user), "App": str(app)},
            {
                "Role": "Mail Recipients",
                "User": str(user),
                "CustomRecipientWriteScope": "unsupported",
            },
        ):
            assert (
                await command(client, tenant, admin, "New-ManagementRoleAssignment", bad)
            ).status_code == 400


async def test_role_state_restore_and_display_metadata_cannot_grant(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        admin = await setup(client, database, settings, tenant, app, user)
        actor = await delegated(database, settings, tenant, app, user)
        assert (
            await command(
                client,
                tenant,
                admin,
                "Add-RoleGroupMember",
                {"Identity": "View-Only Organization Management", "Member": str(user)},
            )
        ).status_code == 200
        route = f"/_mock/tenants/{tenant}/mail/roleState"
        saved = await client.get(route)
        assert saved.status_code == 200, saved.text
        assert (await command(client, tenant, actor, "Get-RemoteDomain", {})).status_code == 200
        assert (
            await command(
                client,
                tenant,
                actor,
                "New-RemoteDomain",
                {"Name": "No write", "DomainName": "no.test"},
            )
        ).status_code == 403
        assert (await client.put(route, json={"members": [], "assignments": []})).status_code == 200
        assert (await command(client, tenant, actor, "Get-RemoteDomain", {})).status_code == 403
        assert (await client.put(route, json=saved.json())).status_code == 200
        assert (await client.get(route)).json() == saved.json()
        # Unknown display-only role metadata is rejected; it never writes authority.
        invalid = await client.put(
            f"/_mock/tenants/{tenant}/mail/collectionState",
            json={"role_groups": [{"Name": "Organization Management", "Members": [str(user)]}]},
        )
        assert invalid.status_code == 422
        assert (
            await command(
                client,
                tenant,
                actor,
                "New-RemoteDomain",
                {"Name": "No write", "DomainName": "no.test"},
            )
        ).status_code == 403


async def test_recipient_group_scope_uses_current_direct_members_only(database, settings):
    from sqlalchemy import delete

    from mockazure.directory.models import DirectoryRelation
    from tests.integration.graph_support import persisted_group

    async with graph_client(database, settings) as (client, tenant, app, user):
        admin = await setup(client, database, settings, tenant, app, user)
        actor = await delegated(database, settings, tenant, app, user)
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant, "ScopedRecipients")
            nested = await persisted_group(session, tenant, "NestedRecipients")
            scope = group.id
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=group.id, object_id=nested.id, relation="member"
                )
            )
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=nested.id, object_id=user, relation="member"
                )
            )
        result = await command(
            client,
            tenant,
            admin,
            "New-ManagementRoleAssignment",
            {
                "Name": "Scoped",
                "Role": "Mail Recipients",
                "User": str(user),
                "RecipientGroupScope": str(scope),
            },
        )
        assert result.status_code == 200, result.text
        # Implicit organization read scope remains available, nested write scope does not.
        assert (
            await command(client, tenant, actor, "Get-Mailbox", {"Identity": str(user)})
        ).status_code == 200
        change = {"Identity": str(user), "ForwardingSmtpAddress": "scope@example.test"}
        assert (await command(client, tenant, actor, "Set-Mailbox", change)).status_code == 403
        async with database.sessions.begin() as session:
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=scope, object_id=user, relation="member"
                )
            )
        assert (await command(client, tenant, actor, "Set-Mailbox", change)).status_code == 200
        async with database.sessions.begin() as session:
            await session.execute(
                delete(DirectoryRelation).where(
                    DirectoryRelation.tenant_id == tenant,
                    DirectoryRelation.group_id == scope,
                    DirectoryRelation.object_id == user,
                )
            )
        assert (
            await command(
                client,
                tenant,
                actor,
                "Set-Mailbox",
                change | {"ForwardingSmtpAddress": "revoked@example.test"},
            )
        ).status_code == 403


async def test_role_mutations_audit_and_invalid_restore_is_atomic(database, settings):
    from uuid import uuid4

    from tests.integration.test_evidence_directory import drain
    from tests.integration.test_mail_audit import observations

    async with graph_client(database, settings) as (client, tenant, app, user):
        admin = await setup(client, database, settings, tenant, app, user)
        assert (
            await command(
                client,
                tenant,
                admin,
                "Add-RoleGroupMember",
                {"Identity": "Recipient Management", "Member": str(user)},
            )
        ).status_code == 200
        route = f"/_mock/tenants/{tenant}/mail/roleState"
        before = (await client.get(route)).json()
        invalid = {
            "members": [{"group_name": "Recipient Management", "principal_id": str(uuid4())}],
            "assignments": [],
        }
        assert (await client.put(route, json=invalid)).status_code == 422
        assert (await client.get(route)).json() == before
        assert (
            await command(
                client,
                tenant,
                admin,
                "Remove-RoleGroupMember",
                {"Identity": "Recipient Management", "Member": str(user)},
            )
        ).status_code == 200
        await drain(database)
        rows = [
            v.payload
            for v in await observations(database, tenant)
            if v.payload["operation"].endswith("-RoleGroupMember")
        ]
        assert {v["operation"] for v in rows} == {"Add-RoleGroupMember", "Remove-RoleGroupMember"}
        assert all(
            v["objectId"] == "Recipient Management"
            and "MailboxOwnerUPN" not in v["auditData"]["dynamicProperties"]
            for v in rows
        )


async def test_role_upgrade_restart_and_lossy_downgrade(database, settings):
    import httpx
    import pytest
    from alembic import command as alembic_command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.admin.app import create_app
    from mockazure.db.migrations import configuration

    async with graph_client(database, settings) as (client, tenant, app, user):
        admin = await setup(client, database, settings, tenant, app, user)
        engine = create_engine(settings.database_url.get_secret_value())
        try:
            config = configuration()
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                alembic_command.downgrade(config, "055")
                assert (
                    connection.scalar(
                        text("SELECT count(*) FROM mail_mailboxes WHERE tenant_id=:tenant"),
                        {"tenant": tenant},
                    )
                    == 1
                )
                alembic_command.upgrade(config, "head")
            result = await command(
                client,
                tenant,
                admin,
                "Add-RoleGroupMember",
                {"Identity": "Recipient Management", "Member": str(user)},
            )
            assert result.status_code == 200, result.text
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(settings, database)),
                base_url="https://localhost:8443",
            ) as restarted:
                result = await command(
                    restarted,
                    tenant,
                    admin,
                    "Get-RoleGroupMember",
                    {"Identity": "Recipient Management"},
                )
                assert result.status_code == 200 and result.json()["value"][0][
                    "ExternalDirectoryObjectId"
                ] == str(user), result.text
            with pytest.raises(DBAPIError, match="Cannot discard operational Exchange role state"):
                with engine.begin() as connection:
                    config.attributes["connection"] = connection
                    alembic_command.downgrade(config, "055")
        finally:
            engine.dispose()
