from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from mockazure.evidence.models import EvidenceSource
from mockazure.identity.models import DirectoryRole
from tests.integration.graph_support import graph_client, headers_for

MOBILE = "3179e48a-750b-4051-897c-87b9720928f7"
EMAIL = "3ddfcfc8-9383-446f-83cc-3ab9be4be18f"
WRITE = {"UserAuthenticationMethod.ReadWrite.All"}


async def test_phone_email_lifecycle(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        auth = await headers_for(database, settings, tenant, app, None, WRITE)
        path = f"/v1.0/users/{user}/authentication"
        for collection, body, identifier, update in (
            (
                "phoneMethods",
                {"phoneNumber": "+1 5555550123", "phoneType": "mobile"},
                MOBILE,
                {"phoneNumber": "+1 5555550124"},
            ),
            (
                "emailMethods",
                {"emailAddress": "recover@example.test"},
                EMAIL,
                {"emailAddress": "updated@example.test"},
            ),
        ):
            created = await client.post(path + "/" + collection, json=body, headers=auth)
            assert created.status_code == 201, created.text
            assert created.json()["id"] == identifier
            item = path + "/" + collection + "/" + identifier
            changed = await client.patch(item, json=update, headers=auth)
            assert changed.status_code == 204 and not changed.content, changed.text
            result = (await client.get(item, headers=auth)).json()
            assert all(result[k] == v for k, v in update.items())
            assert (await client.delete(item, headers=auth)).status_code == 204
            assert (await client.get(item, headers=auth)).status_code == 404
        async with database.sessions() as session:
            facts = list(
                await session.scalars(
                    select(EvidenceSource).where(
                        EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "security"
                    )
                )
            )
            assert len(facts) == 6
            assert all("local-directory-password" not in str(row.facts) for row in facts)


async def test_fixed_method_ids(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        auth = await headers_for(database, settings, tenant, app, None, WRITE)
        path = f"/v1.0/users/{user}/authentication/phoneMethods"
        body = {"phoneNumber": "+1 5555550123", "phoneType": "mobile"}
        assert (await client.post(path, json=body, headers=auth)).status_code == 201
        assert (await client.post(path, json=body, headers=auth)).status_code == 409
        assert (
            await client.patch(path + "/" + MOBILE, json={"phoneType": "office"}, headers=auth)
        ).status_code == 400
        assert (await client.delete(path + "/" + EMAIL, headers=auth)).status_code == 404
        assert (
            await client.post(path + "/" + MOBILE + "/enableSmsSignIn", headers=auth)
        ).status_code == 501
        assert (
            await client.post(path.replace("/v1.0/", "/beta/"), json=body, headers=auth)
        ).status_code == 501


async def test_authenticator_fido_delete(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        auth = await headers_for(database, settings, tenant, app, None, WRITE)
        admin = f"/_mock/tenants/{tenant}/security/users/{user}/authenticationMethods"
        for collection, kind in (
            ("fido2Methods", "fido2AuthenticationMethod"),
            ("microsoftAuthenticatorMethods", "microsoftAuthenticatorAuthenticationMethod"),
        ):
            body = {"@odata.type": "#microsoft.graph." + kind, "displayName": "Lab descriptor"}
            enrolled = await client.post(admin, json=body)
            assert enrolled.status_code == 201
            path = f"/v1.0/users/{user}/authentication/{collection}"
            assert (await client.post(path, json=body, headers=auth)).status_code == 501
            deleted = await client.delete(path + "/" + enrolled.json()["id"], headers=auth)
            assert deleted.status_code == 204, deleted.text


@pytest.mark.parametrize(
    "operator_role,target_role,expected",
    [
        ("Global Reader", None, 403),
        ("Authentication Administrator", None, 201),
        ("Authentication Administrator", "Authentication Administrator", 201),
        ("Authentication Administrator", "Global Administrator", 403),
        ("Authentication Administrator", "Security Reader", 403),
        ("Privileged Authentication Administrator", "Global Administrator", 201),
    ],
)
async def test_self_and_privileged_target_matrix(
    database, settings, operator_role, target_role, expected
):
    async with graph_client(database, settings) as (client, tenant, app, user):
        response = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "operator@contoso.test",
                "display_name": "Operator",
                "password": "local-password",
            },
        )
        operator = UUID(response.json()["id"])
        async with database.sessions.begin() as session:
            session.add(DirectoryRole(tenant_id=tenant, user_id=operator, role=operator_role))
            if target_role:
                session.add(DirectoryRole(tenant_id=tenant, user_id=user, role=target_role))
        auth = await headers_for(database, settings, tenant, app, operator, WRITE, "delegated")
        body = {"emailAddress": "recovery@example.test"}
        result = await client.post(
            f"/v1.0/users/{user}/authentication/emailMethods", json=body, headers=auth
        )
        assert result.status_code == expected, result.text
        for collection in ("emailMethods", "phoneMethods"):
            own = await client.post(
                f"/v1.0/me/authentication/{collection}", json=body, headers=auth
            )
            assert own.status_code == 403, own.text


async def test_no_secret_inventory_fields(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        auth = await headers_for(database, settings, tenant, app, None, WRITE)
        path = f"/v1.0/users/{user}/authentication/emailMethods"
        for field in ("password", "secret", "id", "verifier"):
            result = await client.post(
                path,
                json={"emailAddress": "recovery@example.test", field: "NEVER-PERSIST"},
                headers=auth,
            )
            assert result.status_code == 400, result.text
        assert (await client.get(path, headers=auth)).json()["value"] == []
        assert (await client.delete(path + "/" + str(uuid4()), headers=auth)).status_code == 404
