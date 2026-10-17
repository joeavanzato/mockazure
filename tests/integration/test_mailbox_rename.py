from sqlalchemy import select

from mockazure.evidence.models import EvidenceSource
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_mailbox_state import actor, provision


async def test_mailbox_rename_uses_verified_domain_and_preserves_identity_and_audit(
    database, settings
):
    from datetime import UTC, datetime

    from mockazure.mail.mailboxes import mutate_mailbox
    from mockazure.mail.repository import resolve_mailbox

    async with graph_client(database, settings) as (client, tenant, app, user):
        original = await provision(client, tenant, user)
        assert (
            await client.post(
                f"/_mock/tenants/{tenant}/mail/grants",
                json={
                    "client_id": str(app),
                    "commands": ["Set-Mailbox"],
                },
            )
        ).status_code == 201
        async with database.sessions.begin() as session:
            box = await resolve_mailbox(session, tenant, str(user))
            await mutate_mailbox(
                session,
                actor(tenant, app),
                box,
                {
                    "ForwardingSmtpAddress": "outside@example.test",
                },
                datetime.now(UTC),
            )
        headers = await headers_for(database, settings, tenant, app, None, {"User.ReadWrite.All"})
        for invalid in ("name@foreign.test", "name name@contoso.test", "é@contoso.test", None):
            response = await client.patch(
                f"/v1.0/users/{user}", headers=headers, json={"userPrincipalName": invalid}
            )
            assert response.status_code == 400
        second = (
            await client.post(
                f"/_mock/tenants/{tenant}/identity/users",
                json={
                    "upn": "taken@contoso.test",
                    "display_name": "Other",
                    "password": "local-other-password",
                },
            )
        ).json()
        assert "id" in second
        duplicate = await client.patch(
            f"/v1.0/users/{user}", headers=headers, json={"userPrincipalName": "TAKEN@CONTOSO.TEST"}
        )
        assert duplicate.status_code == 409
        response = await client.patch(
            f"/v1.0/users/{user}",
            headers=headers,
            json={"userPrincipalName": "RENAMED@CONTOSO.TEST"},
        )
        assert response.status_code == 204, response.text
        saved = (
            await client.get(f"/_mock/tenants/{tenant}/mail/mailboxes/renamed@contoso.test")
        ).json()
        assert saved["ExchangeGuid"] == original["ExchangeGuid"]
        assert saved["UserPrincipalName"] == "renamed@contoso.test"
        assert saved["PrimarySmtpAddress"] == "reader@contoso.test"
        async with database.sessions() as session:
            fact = await session.scalar(
                select(EvidenceSource).where(
                    EvidenceSource.tenant_id == tenant,
                    EvidenceSource.kind == "mail",
                )
            )
            assert fact.facts["mailbox"]["upn"] == "reader@contoso.test"
            assert fact.facts["after"]["UserPrincipalName"] == "reader@contoso.test"
