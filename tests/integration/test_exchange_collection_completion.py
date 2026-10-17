"""Exchange evidence uses canonical state and never grants command authority."""

from uuid import uuid4

import httpx

from mockazure.admin.app import create_app
from tests.integration.graph_support import graph_client
from tests.integration.test_exchange_mail import command, exchange_headers
from tests.integration.test_mailbox_state import provision


def fixture(user):
    return {
        "organization": {"AuditDisabled": True, "OAuth2ClientProfileEnabled": True},
        "transport": {"SmtpClientAuthenticationDisabled": True},
        "accepted_domains": [
            {
                "Guid": str(uuid4()),
                "Name": "Contoso",
                "DomainName": "contoso.test",
                "DomainType": "Authoritative",
                "Default": True,
            }
        ],
        "transport_rules": [
            {
                "Guid": str(uuid4()),
                "Name": "External forwarding review",
                "Priority": 0,
                "State": "Enabled",
                "Mode": "Audit",
                "Comments": "Imported evidence",
            }
        ],
        "mailbox_permissions": [
            {
                "id": str(uuid4()),
                "mailbox_user_id": str(user),
                "User": "NT AUTHORITY\\SELF",
                "AccessRights": ["FullAccess", "ReadPermission"],
                "IsInherited": False,
                "Deny": False,
            },
            {
                "id": str(uuid4()),
                "mailbox_user_id": str(user),
                "User": "auditor@contoso.test",
                "AccessRights": ["FullAccess"],
                "IsInherited": True,
                "Deny": True,
            },
        ],
        "recipient_permissions": [
            {
                "id": str(uuid4()),
                "mailbox_user_id": str(user),
                "Trustee": "auditor@contoso.test",
                "AccessRights": ["SendAs"],
                "AccessControlType": "Allow",
                "IsInherited": False,
                "InheritanceType": "None",
            }
        ],
    }


async def setup(client, tenant, app, user):
    await provision(client, tenant, user, aliases=["alias@contoso.test"])
    response = await client.put(
        f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
    )
    assert response.status_code == 200, response.text
    data = fixture(user)
    response = await client.put(f"/_mock/tenants/{tenant}/mail/collectionState", json=data)
    assert response.status_code == 200, response.text
    return data


async def test_exchange_configuration_persists_and_exports_typed_state(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        data = await setup(client, tenant, app, user)
        headers = await exchange_headers(database, settings, tenant, app)
        expected = {
            "Get-OrganizationConfig": ("AuditDisabled", True),
            "Get-TransportConfig": ("SmtpClientAuthenticationDisabled", True),
            "Get-AcceptedDomain": ("DomainName", "contoso.test"),
            "Get-TransportRule": ("Mode", "Audit"),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as second:
            for name, (field, value) in expected.items():
                response = await command(second, tenant, headers, name)
                assert response.status_code == 200, response.text
                assert response.json()["value"][0][field] == value
        exported = await client.get(f"/_mock/tenants/{tenant}/mail/collectionState")
        assert exported.status_code == 200, exported.text
        assert exported.json() == data
        reset = await client.put(f"/_mock/tenants/{tenant}/mail/collectionState", json={})
        assert reset.status_code == 200, reset.text
        assert (await command(client, tenant, headers, "Get-TransportRule")).json()["value"] == []
        restored = await client.put(
            f"/_mock/tenants/{tenant}/mail/collectionState", json=exported.json()
        )
        assert restored.status_code == 200, restored.text
        assert (await command(client, tenant, headers, "Get-TransportRule")).json()["value"][0][
            "Name"
        ] == data["transport_rules"][0]["Name"]


async def test_exchange_permission_rows_resolve_aliases_and_preserve_deny(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant, app, user)
        headers = await exchange_headers(database, settings, tenant, app)
        for identity in [str(user), "reader", "alias@contoso.test"]:
            response = await command(
                client, tenant, headers, "Get-MailboxPermission", {"Identity": identity}
            )
            assert response.status_code == 200, response.text
            rows = response.json()["value"]
            assert len(rows) == 2
            assert all(row["Identity"] == "reader@contoso.test" for row in rows)
            deny = next(row for row in rows if row["User"] == "auditor@contoso.test")
            assert deny["Deny"] is True and deny["IsInherited"] is True
        selected = await command(
            client,
            tenant,
            headers,
            "Get-MailboxPermission",
            {"Identity": str(user), "User": "AUDITOR@CONTOSO.TEST"},
        )
        assert len(selected.json()["value"]) == 1
        recipient = await command(
            client,
            tenant,
            headers,
            "Get-RecipientPermission",
            {"Identity": str(user), "Trustee": "auditor@contoso.test", "AccessRights": ["SendAs"]},
        )
        assert recipient.status_code == 200, recipient.text
        assert recipient.json()["value"][0]["AccessRights"] == ["SendAs"]


async def test_exchange_permission_evidence_is_not_authority_and_scopes_revalidate(
    database, settings
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant, app, user)
        headers = await exchange_headers(database, settings, tenant, app)
        revoked = await client.delete(
            f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
        )
        assert revoked.status_code == 200, revoked.text
        assert (
            await command(client, tenant, headers, "Get-MailboxPermission", {"Identity": str(user)})
        ).status_code == 403
        missing = await command(
            client, tenant, headers, "Get-MailboxPermission", {"Identity": str(uuid4())}
        )
        assert missing.status_code == 403, missing.text
        grant = await client.post(
            f"/_mock/tenants/{tenant}/mail/grants",
            json={
                "client_id": str(app),
                "mailbox_user_id": str(user),
                "commands": [
                    "Get-MailboxPermission",
                    "Get-RecipientPermission",
                    "Get-TransportConfig",
                ],
            },
        )
        assert grant.status_code == 201, grant.text
        assert (
            await command(client, tenant, headers, "Get-MailboxPermission", {"Identity": str(user)})
        ).status_code == 200
        # A mailbox-scoped grant must not expose tenant-wide configuration.
        assert (await command(client, tenant, headers, "Get-TransportConfig")).status_code == 403
        other = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "other@contoso.test",
                "display_name": "Reader",
                "password": "local-only-password",
            },
        )
        other_id = other.json()["id"]
        seeded = await client.put(
            f"/_mock/tenants/{tenant}/mail/mailboxes/{other_id}",
            json={"primary_smtp": "other@contoso.test", "alias": "other"},
        )
        assert seeded.status_code == 200, seeded.text
        assert (
            await command(client, tenant, headers, "Get-MailboxPermission", {"Identity": other_id})
        ).status_code == 403
        # Enumeration filters both mailbox authority and trustee before ResultSize.
        data = fixture(user)
        denied_row = dict(
            data["recipient_permissions"][0], id=str(uuid4()), mailbox_user_id=other_id
        )
        unrelated = dict(
            data["recipient_permissions"][0], id=str(uuid4()), Trustee="someone@contoso.test"
        )
        data["recipient_permissions"] = [denied_row, unrelated, data["recipient_permissions"][0]]
        imported = await client.put(f"/_mock/tenants/{tenant}/mail/collectionState", json=data)
        assert imported.status_code == 200, imported.text
        enumerated = await command(
            client,
            tenant,
            headers,
            "Get-RecipientPermission",
            {"Trustee": "AUDITOR@CONTOSO.TEST", "ResultSize": "1"},
        )
        assert enumerated.status_code == 200, enumerated.text
        rows = enumerated.json()["value"]
        assert len(rows) == 1
        assert rows[0]["Identity"] == "reader@contoso.test"
        assert rows[0]["Trustee"] == "auditor@contoso.test"
        assert (
            await client.delete(f"/_mock/tenants/{tenant}/mail/grants/{grant.json()['id']}")
        ).status_code == 204
        assert (
            await command(client, tenant, headers, "Get-MailboxPermission", {"Identity": str(user)})
        ).status_code == 403


async def test_exchange_collection_rejects_invalid_imports_parameters_and_unknown_identity(
    database, settings
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        data = await setup(client, tenant, app, user)
        headers = await exchange_headers(database, settings, tenant, app)
        for name, parameters in [
            ("Get-MailboxPermission", {"Identity": str(user), "Owner": True}),
            ("Get-TransportRule", {"Filter": "arbitrary"}),
            ("Get-RecipientPermission", {"AccessRights": ["FullAccess"]}),
            ("Get-TransportRule", {"State": "Imaginary"}),
        ]:
            response = await command(client, tenant, headers, name, parameters)
            assert response.status_code == 400, response.text
        assert (
            await command(client, tenant, headers, "Get-AcceptedDomain", {"Identity": "missing"})
        ).status_code == 404
        data["accepted_domains"][0]["DomainType"] = "ExternalRelay"
        assert (
            await client.put(f"/_mock/tenants/{tenant}/mail/collectionState", json=data)
        ).status_code == 422
        data["accepted_domains"][0]["DomainType"] = "Authoritative"
        data["transport_rules"][0]["From"] = ["bad\0sender"]
        bad_text = await client.put(f"/_mock/tenants/{tenant}/mail/collectionState", json=data)
        assert bad_text.status_code == 422, bad_text.text
        data["transport_rules"][0].pop("From")
        data["organization"]["Invented"] = True
        assert (
            await client.put(f"/_mock/tenants/{tenant}/mail/collectionState", json=data)
        ).status_code == 422
        data["organization"].pop("Invented")
        data["mailbox_permissions"][0]["mailbox_user_id"] = str(uuid4())
        assert (
            await client.put(f"/_mock/tenants/{tenant}/mail/collectionState", json=data)
        ).status_code == 404
        state = (await client.get(f"/_mock/tenants/{tenant}/mail/collectionState")).json()
        assert state["organization"]["AuditDisabled"] is True
        assert state["mailbox_permissions"][0]["mailbox_user_id"] == str(user)


async def test_exchange_evidence_excludes_deleted_mailboxes_and_rejects_lossy_downgrade(
    database, settings
):
    import pytest
    from alembic import command as migrations
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration
    from tests.integration.graph_support import headers_for

    async with graph_client(database, settings) as (client, tenant, app, user):
        await setup(client, tenant, app, user)
        engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
        try:
            with pytest.raises(DBAPIError, match="Export and clear Exchange"):
                with engine.begin() as connection:
                    config = configuration()
                    config.attributes["connection"] = connection
                    migrations.downgrade(config, "040")
        finally:
            engine.dispose()
        actor = await headers_for(database, settings, tenant, app, None, {"User.ReadWrite.All"})
        deleted = await client.delete(f"/v1.0/users/{user}", headers=actor)
        assert deleted.status_code == 204, deleted.text
        state = (await client.get(f"/_mock/tenants/{tenant}/mail/collectionState")).json()
        assert state["mailbox_permissions"] == [] and state["recipient_permissions"] == []
        restored = await client.put(f"/_mock/tenants/{tenant}/mail/collectionState", json=state)
        assert restored.status_code == 200, restored.text


async def test_exchange_upgrade_preserves_mail_settings(settings):
    from alembic import command as migrations
    from sqlalchemy import create_engine, text

    from mockazure.db.migrations import configuration, migrate

    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    tenant = uuid4()
    try:
        with engine.begin() as connection:
            config = configuration()
            config.attributes["connection"] = connection
            migrations.upgrade(config, "040")
            connection.execute(
                text(
                    "INSERT INTO tenants (id,slug,display_name,domain,licences,revision) "
                    "VALUES (:tenant,'exchange','Exchange','exchange.test','[]',1)"
                ),
                {"tenant": tenant},
            )
            connection.execute(
                text(
                    "INSERT INTO mail_settings (tenant_id,exchange_enabled,audit_tier) "
                    "VALUES (:tenant,true,'premium')"
                ),
                {"tenant": tenant},
            )
        migrate(settings.database_url.get_secret_value())
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT audit_tier FROM mail_settings")) == "premium"
            assert connection.scalar(text("SELECT count(*) FROM mail_exchange_configuration")) == 0
    finally:
        engine.dispose()
