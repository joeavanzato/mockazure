"""Recipient identity, operational grants and display-only evidence stay distinct."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.evidence.models import EvidenceSource
from mockazure.identity.types import PrincipalContext
from tests.integration.graph_support import graph_client
from tests.integration.test_exchange_mail import command, exchange_headers
from tests.integration.test_mailbox_state import provision


async def setup(client, database, settings, tenant, app, owner):
    await provision(client, tenant, owner, aliases=["alias@contoso.test"])
    result = await client.put(
        f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
    )
    assert result.status_code == 200
    created = await client.post(
        f"/_mock/tenants/{tenant}/identity/users",
        json={
            "upn": "delegate@contoso.test",
            "display_name": "Directory Reader",
            "password": "local-delegate-password",
        },
    )
    assert created.status_code == 201
    delegate = UUID(created.json()["id"])
    await provision(
        client, tenant, delegate, primary_smtp="delegate@contoso.test", alias="delegate"
    )
    return await exchange_headers(database, settings, tenant, app), delegate


def sender(tenant, app, user):
    return PrincipalContext(
        tenant,
        app,
        user,
        "delegated",
        "00000003-0000-0000-c000-000000000000",
        frozenset({"Mail.Send.Shared"}),
    )


async def test_recipient_identity_and_delegation(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        for identity in (str(owner), "reader@contoso.test", "READER", "ALIAS@CONTOSO.TEST"):
            response = await command(
                client, tenant, headers, "Get-Recipient", {"Identity": identity}
            )
            assert response.status_code == 200, response.text
            assert response.json()["value"][0]["ExternalDirectoryObjectId"] == str(owner)
        ambiguous = await command(
            client, tenant, headers, "Get-Recipient", {"Identity": "Directory Reader"}
        )
        assert ambiguous.status_code == 400
        missing = await command(
            client, tenant, headers, "Get-Recipient", {"Identity": str(uuid4())}
        )
        assert missing.status_code == 404
        grant = {
            "Identity": str(owner),
            "Trustee": "delegate@contoso.test",
            "AccessRights": ["SendAs"],
        }
        result = await command(client, tenant, headers, "Add-RecipientPermission", grant)
        assert result.status_code == 200, result.text
        permissions = await command(
            client, tenant, headers, "Get-RecipientPermission", {"Identity": str(owner)}
        )
        assert permissions.status_code == 200
        assert any(
            row["Trustee"] == "delegate@contoso.test" and row["AccessRights"] == ["SendAs"]
            for row in permissions.json()["value"]
        )
        from mockazure.mail.delegation import require_send_identity

        async with database.sessions() as session:
            await require_send_identity(
                session, sender(tenant, app, delegate), delegate, owner, "sendAs"
            )
        removed = await command(client, tenant, headers, "Remove-RecipientPermission", grant)
        assert removed.status_code == 200, removed.text
        async with database.sessions() as session:
            with pytest.raises(AuthorizationDenied):
                await require_send_identity(
                    session, sender(tenant, app, delegate), delegate, owner, "sendAs"
                )
        changed = await command(
            client,
            tenant,
            headers,
            "Set-Mailbox",
            {
                "Identity": str(owner),
                "GrantSendOnBehalfTo": [str(delegate)],
            },
        )
        assert changed.status_code == 200, changed.text
        mailbox = await command(client, tenant, headers, "Get-Mailbox", {"Identity": str(owner)})
        assert set(mailbox.json()["value"][0]["GrantSendOnBehalfTo"]) == {str(delegate)}
        async with database.sessions() as session:
            await require_send_identity(
                session, sender(tenant, app, delegate), delegate, owner, "sendOnBehalf"
            )
            with pytest.raises(AuthorizationDenied):
                await require_send_identity(
                    session, sender(tenant, app, delegate), delegate, owner, "sendAs"
                )
        cleared = await command(
            client,
            tenant,
            headers,
            "Set-Mailbox",
            {
                "Identity": str(owner),
                "GrantSendOnBehalfTo": {"Remove": ["delegate@contoso.test"]},
            },
        )
        assert cleared.status_code == 200, cleared.text
        async with database.sessions() as session:
            with pytest.raises(AuthorizationDenied):
                await require_send_identity(
                    session, sender(tenant, app, delegate), delegate, owner, "sendOnBehalf"
                )
            sources = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "mail"
                    )
                )
            )
            operations = [row.facts["operation"] for row in sources]
            assert (
                "Add-RecipientPermission" in operations
                and "Remove-RecipientPermission" in operations
            )
            assert operations.count("Set-Mailbox") == 2


async def test_imported_permissions_do_not_grant_operational_access(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        imported = await client.put(
            f"/_mock/tenants/{tenant}/mail/collectionState",
            json={
                "recipient_permissions": [
                    {
                        "id": str(uuid4()),
                        "mailbox_user_id": str(owner),
                        "Trustee": "delegate@contoso.test",
                        "AccessRights": ["SendAs"],
                        "AccessControlType": "Allow",
                        "IsInherited": False,
                        "InheritanceType": "None",
                    }
                ],
            },
        )
        assert imported.status_code == 200, imported.text
        from mockazure.mail.delegation import require_send_identity

        async with database.sessions() as session:
            with pytest.raises(AuthorizationDenied):
                await require_send_identity(
                    session, sender(tenant, app, delegate), delegate, owner, "sendAs"
                )
        added = await command(
            client,
            tenant,
            headers,
            "Add-MailboxPermission",
            {
                "Identity": str(owner),
                "User": str(delegate),
                "AccessRights": ["FullAccess"],
                "InheritanceType": "All",
                "AutoMapping": False,
            },
        )
        assert added.status_code == 200, added.text
        read = await command(
            client, tenant, headers, "Get-MailboxPermission", {"Identity": str(owner)}
        )
        assert any(
            row["User"] == "delegate@contoso.test" and row["AccessRights"] == ["FullAccess"]
            for row in read.json()["value"]
        )
        # FullAccess opens content; it does not confer SendAs or management authority.
        async with database.sessions() as session:
            with pytest.raises(AuthorizationDenied):
                await require_send_identity(
                    session, sender(tenant, app, delegate), delegate, owner, "sendAs"
                )


async def test_compiled_recipient_paths_and_current_authorization(database, settings):
    import base64

    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        key = base64.b64encode(b"reader@contoso.test").decode()
        root = f"/adminapi/beta/{tenant}"
        by_header = await client.get(
            root + "/Recipient",
            params={"ReadIdsAndParamsFromHeaders": "True"},
            headers=headers | {"XParameter-Identity": key},
        )
        assert by_header.status_code == 200, by_header.text
        assert by_header.json()["value"][0]["ExternalDirectoryObjectId"] == str(owner)
        by_key = await client.get(
            root + f"/Recipient('{key}')", params={"isEncoded": "true"}, headers=headers
        )
        assert by_key.status_code == 200, by_key.text
        assert by_key.json()["ExternalDirectoryObjectId"] == str(owner)
        for name, who, right in [
            ("Mailbox", "User", "FullAccess"),
            ("Recipient", "Trustee", "SendAs"),
        ]:
            result = await command(
                client,
                tenant,
                headers,
                f"Add-{name}Permission",
                {"Identity": str(owner), who: str(delegate), "AccessRights": [right]},
            )
            assert result.status_code == 200, result.text
        box_permissions = await client.get(
            root + f"/Mailbox('{key}')/MailboxPermission",
            params={"isEncoded": "true"},
            headers=headers,
        )
        assert box_permissions.status_code == 200, box_permissions.text
        assert box_permissions.json()["value"][0]["PermissionList"][0]["AccessRights"] == [
            "FullAccess"
        ]
        assert box_permissions.json()["value"][0]["PermissionId"]
        assert box_permissions.json()["value"][0]["MailboxIdentity"] == "reader@contoso.test"
        recipient_permissions = await client.get(
            root + f"/Recipient('{key}')",
            params={
                "isEncoded": "true",
                "$expand": "RecipientPermission",
                "$select": "RecipientPermission, ExternalDirectoryObjectId",
            },
            headers=headers,
        )
        assert recipient_permissions.status_code == 200, recipient_permissions.text
        assert recipient_permissions.json()["RecipientPermission"][0]["AccessRights"] == ["SendAs"]
        await client.delete(
            f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
        )
        assert (await client.get(root + f"/Recipient('{key}')", headers=headers)).status_code == 403
        assert (
            await client.get(root + f"/Mailbox('{key}')/MailboxPermission", headers=headers)
        ).status_code == 403


async def test_full_access_shared_scope_and_lifecycle(database, settings):
    from dataclasses import replace

    from mockazure.mail.authorization import require_mailbox_access
    from mockazure.mail.repository import resolve_mailbox

    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        actor = replace(sender(tenant, app, delegate), permissions=frozenset({"Mail.Read.Shared"}))
        permission = {"Identity": str(owner), "User": str(delegate), "AccessRights": ["FullAccess"]}
        added = await command(client, tenant, headers, "Add-MailboxPermission", permission)
        assert added.status_code == 200, added.text
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(owner))
            await require_mailbox_access(session, actor, box, "content.read")
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(
                    session,
                    replace(actor, permissions=frozenset({"Mail.Read"})),
                    box,
                    "content.read",
                )
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, actor, box, "content.write")
        lifecycle = f"/_mock/tenants/{tenant}/mail/delegationState"
        exported = await client.get(lifecycle)
        assert exported.status_code == 200, exported.text
        assert len(exported.json()["grants"]) == 1
        assert (await client.put(lifecycle, json={"grants": []})).status_code == 200
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(owner))
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, actor, box, "content.read")
        assert (await client.put(lifecycle, json=exported.json())).status_code == 200
        denied = await command(
            client, tenant, headers, "Add-MailboxPermission", permission | {"Deny": True}
        )
        assert denied.status_code == 200, denied.text
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(owner))
            with pytest.raises(AuthorizationDenied):
                await require_mailbox_access(session, actor, box, "content.read")
        assert (
            await command(
                client, tenant, headers, "Remove-MailboxPermission", permission | {"Deny": True}
            )
        ).status_code == 200
        async with database.sessions() as session:
            box = await resolve_mailbox(session, tenant, str(owner))
            await require_mailbox_access(session, actor, box, "content.read")


async def test_shared_graph_content_and_permission_evidence(database, settings):
    from tests.integration.graph_support import headers_for
    from tests.integration.test_evidence_directory import drain
    from tests.integration.test_mail_audit import observations
    from tests.integration.test_mail_content import seed_mail

    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        await seed_mail(client, database, tenant, owner)
        await client.patch(f"/_mock/tenants/{tenant}/evidence/settings", json={"delay_seconds": 0})
        grant = {"Identity": str(owner), "User": str(delegate), "AccessRights": ["FullAccess"]}
        assert (
            await command(client, tenant, headers, "Add-MailboxPermission", grant)
        ).status_code == 200
        graph = await headers_for(
            database, settings, tenant, app, delegate, {"Mail.Read.Shared"}, kind="delegated"
        )
        path = f"/v1.0/users/{owner}/messages/m1"
        read = await client.get(path, headers=graph)
        assert read.status_code == 200, read.text
        assert read.json()["body"]["content"] == "<p>Secret body</p>"
        basic = await headers_for(
            database,
            settings,
            tenant,
            app,
            delegate,
            {"Mail.Read", "Mail.ReadBasic.Shared"},
            kind="delegated",
        )
        limited = await client.get(path + "?$select=id,body", headers=basic)
        assert limited.status_code == 200 and "body" not in limited.json(), limited.text
        assert (await client.get(path + "/$value", headers=basic)).status_code == 403
        assert (await client.get(path + "/$value", headers=graph)).status_code == 200
        write = await headers_for(
            database, settings, tenant, app, delegate, {"Mail.ReadWrite.Shared"}, kind="delegated"
        )
        assert (await client.patch(path, headers=write, json={"isRead": True})).status_code == 200
        assert (
            await command(client, tenant, headers, "Remove-MailboxPermission", grant)
        ).status_code == 200
        assert (await client.get(path, headers=graph)).status_code == 403
        await drain(database)
        rows = [
            v
            for v in await observations(database, tenant)
            if v.payload["operation"] in {"Add-MailboxPermission", "Remove-MailboxPermission"}
        ]
        assert len(rows) == 2
        assert {v.payload["objectId"] for v in rows} == {"reader@contoso.test"}


async def test_send_as_export_roundtrip_and_atomic_invalid_change(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        assert (
            await command(
                client,
                tenant,
                headers,
                "Add-RecipientPermission",
                {"Identity": str(owner), "Trustee": str(delegate), "AccessRights": ["SendAs"]},
            )
        ).status_code == 200
        path = f"/_mock/tenants/{tenant}/mail/delegationState"
        exported = await client.get(path)
        assert exported.status_code == 200, exported.text
        assert exported.json()["grants"][0]["auto_mapping"] is False
        invalid = await command(
            client,
            tenant,
            headers,
            "Set-Mailbox",
            {
                "Identity": str(owner),
                "ForwardingSmtpAddress": "smtp:changed@contoso.test",
                "GrantSendOnBehalfTo": [str(delegate), str(uuid4())],
            },
        )
        assert invalid.status_code == 404, invalid.text
        box = (
            await command(client, tenant, headers, "Get-Mailbox", {"Identity": str(owner)})
        ).json()["value"][0]
        assert box["ForwardingSmtpAddress"] is None and box["GrantSendOnBehalfTo"] == []
        corrupted = {"grants": [dict(exported.json()["grants"][0], principal_id=str(uuid4()))]}
        assert (await client.put(path, json=corrupted)).status_code == 422
        assert (await client.get(path)).json() == exported.json()
        assert (await client.put(path, json={"grants": []})).status_code == 200
        assert (await client.put(path, json=exported.json())).json() == exported.json()


async def test_recipient_isolation_paging_filters_and_scope(database, settings):
    from urllib.parse import urlsplit

    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        other_tenant = (
            await client.post(
                "/_mock/tenants",
                json={"slug": "other", "domain": "other.test", "display_name": "Other"},
            )
        ).json()["id"]
        other_user = (
            await client.post(
                f"/_mock/tenants/{other_tenant}/identity/users",
                json={
                    "upn": "reader@other.test",
                    "display_name": "Directory Reader",
                    "password": "other-local-password",
                },
            )
        ).json()["id"]
        await provision(client, other_tenant, other_user, primary_smtp="reader@other.test")
        for name, params in [
            ("Get-Recipient", {"Identity": other_user}),
            (
                "Add-RecipientPermission",
                {"Identity": str(owner), "Trustee": other_user, "AccessRights": ["SendAs"]},
            ),
        ]:
            result = await command(client, tenant, headers, name, params)
            assert result.status_code == 404, result.text
        root = f"/adminapi/beta/{tenant}/Recipient"
        first = await client.get(root, headers=headers | {"Prefer": "odata.maxpagesize=1;"})
        assert first.status_code == 200, first.text
        link = urlsplit(first.json()["@odata.nextLink"])
        last = await client.get(
            link.path + "?" + link.query, headers=headers | {"Prefer": "odata.maxpagesize=1;"}
        )
        ids = {
            row["ExternalDirectoryObjectId"]
            for page in [first, last]
            for row in page.json()["value"]
        }
        assert ids == {str(owner), str(delegate)} and "@odata.nextLink" not in last.json()
        filtered = await command(
            client,
            tenant,
            headers,
            "Get-Recipient",
            {"Filter": "Alias -like 'read*'", "SortBy": "-DisplayName"},
        )
        assert [v["ExternalDirectoryObjectId"] for v in filtered.json()["value"]] == [str(owner)]
        for query in [
            {"$filter": "DisplayName eq 'x' or true"},
            {"PropertySet": "Invented"},
            {"RecipientType": "MailContact"},
            {"$select": "password"},
            {"ReadIdsAndParamsFromHeaders": "false"},
        ]:
            assert (await client.get(root, params=query, headers=headers)).status_code == 400
        await client.delete(
            f"/_mock/tenants/{tenant}/identity/applications/{app}/roles/Exchange Administrator"
        )
        grant = await client.post(
            f"/_mock/tenants/{tenant}/mail/grants",
            json={
                "client_id": str(app),
                "mailbox_user_id": str(delegate),
                "commands": ["Get-Recipient", "Get-EXORecipient"],
            },
        )
        assert grant.status_code == 201, grant.text
        scoped = await client.get(root, headers=headers | {"Prefer": "odata.maxpagesize=1;"})
        assert [v["ExternalDirectoryObjectId"] for v in scoped.json()["value"]] == [str(delegate)]
        assert "@odata.nextLink" not in scoped.json()


async def test_security_group_delegation_tracks_membership(database, settings):
    from sqlalchemy import delete

    from mockazure.directory.models import DirectoryRelation
    from mockazure.mail.delegation import require_send_identity
    from tests.integration.graph_support import persisted_group

    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=group_id, object_id=delegate, relation="member"
                )
            )
        permission = {"Identity": str(owner), "Trustee": str(group_id), "AccessRights": ["SendAs"]}
        added = await command(client, tenant, headers, "Add-RecipientPermission", permission)
        assert added.status_code == 200, added.text
        async with database.sessions() as session:
            await require_send_identity(
                session, sender(tenant, app, delegate), delegate, owner, "sendAs"
            )
        async with database.sessions.begin() as session:
            await session.execute(
                delete(DirectoryRelation).where(
                    DirectoryRelation.tenant_id == tenant, DirectoryRelation.group_id == group_id
                )
            )
        async with database.sessions() as session:
            with pytest.raises(AuthorizationDenied):
                await require_send_identity(
                    session, sender(tenant, app, delegate), delegate, owner, "sendAs"
                )


async def test_permission_mutation_returns_target_and_audits_auto_mapping(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, owner):
        headers, delegate = await setup(client, database, settings, tenant, app, owner)
        values = {"Identity": str(owner), "User": str(delegate), "AccessRights": ["FullAccess"]}
        assert (
            await command(client, tenant, headers, "Add-MailboxPermission", values)
        ).status_code == 200
        assert (
            await command(
                client, tenant, headers, "Add-MailboxPermission", values | {"AutoMapping": False}
            )
        ).status_code == 200
        async with database.sessions() as session:
            sources = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "mail"
                    )
                )
            )
            assert len(sources) == 2
        only_target = await command(
            client, tenant, headers, "Add-MailboxPermission", values | {"User": str(owner)}
        )
        assert len(only_target.json()["value"]) == 1
        assert only_target.json()["value"][0]["User"] == "reader@contoso.test"


async def test_shared_graph_scopes_also_allow_own_mail(database, settings):
    from tests.integration.graph_support import headers_for
    from tests.integration.test_mail_content import seed_mail

    async with graph_client(database, settings) as (client, tenant, app, owner):
        await seed_mail(client, database, tenant, owner)
        read = await headers_for(
            database, settings, tenant, app, owner, {"Mail.Read.Shared"}, kind="delegated"
        )
        result = await client.get("/v1.0/me/messages/m1", headers=read)
        assert result.status_code == 200, result.text
        assert result.json()["body"]["content"] == "<p>Secret body</p>"
        write = await headers_for(
            database, settings, tenant, app, owner, {"Mail.ReadWrite.Shared"}, kind="delegated"
        )
        result = await client.patch("/v1.0/me/messages/m1", headers=write, json={"isRead": True})
        assert result.status_code == 200 and result.json()["isRead"] is True, result.text
