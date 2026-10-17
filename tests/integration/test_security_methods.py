"""Catch fabricated inventory, permission bypass and credential-bearing enrollments."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from mockazure.identity.models import User
from mockazure.providers.graph.schema_reference import validate_payload
from tests.integration.graph_support import graph_client, headers_for

PASSWORD = "28c10230-6103-485e-b985-444c60001490"
MOBILE = "3179e48a-750b-4051-897c-87b9720928f7"


async def test_methods_inventory_and_masking(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path = f"/v1.0/users/{user}/authentication"
        admin = f"/_mock/tenants/{tenant}/security/users/{user}/authenticationMethods"
        headers = await headers_for(
            database, settings, tenant, app, None, {"UserAuthenticationMethod.Read.All"}
        )
        initial = await client.get(path + "/methods", headers=headers)
        assert initial.status_code == 200, initial.text
        assert initial.json()["value"] == [
            {
                "@odata.type": "#microsoft.graph.passwordAuthenticationMethod",
                "id": PASSWORD,
                "password": None,
                "createdDateTime": None,
            }
        ]
        descriptors = [
            {
                "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                "phoneType": "mobile",
                "phoneNumber": "+1 5555550123",
            },
            {
                "@odata.type": "#microsoft.graph.emailAuthenticationMethod",
                "emailAddress": "recovery@example.test",
            },
            {
                "@odata.type": "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod",
                "displayName": "Lab handset",
                "deviceTag": "SoftwareTokenActivated",
            },
            {
                "@odata.type": "#microsoft.graph.fido2AuthenticationMethod",
                "displayName": "Lab key",
                "model": "Inventory key",
            },
        ]
        for descriptor in descriptors:
            result = await client.post(admin, json=descriptor)
            assert result.status_code == 201, result.text
        inventory = await client.get(path + "/methods", headers=headers)
        assert len(inventory.json()["value"]) == 5
        for value in inventory.json()["value"]:
            validate_payload("v1.0", value["@odata.type"].split(".")[-1], value, partial=True)
            item = await client.get(path + "/methods/" + value["id"], headers=headers)
            assert item.status_code == 200 and item.json()["id"] == value["id"]
        for collection in (
            "passwordMethods",
            "phoneMethods",
            "emailMethods",
            "microsoftAuthenticatorMethods",
            "fido2Methods",
        ):
            collection_headers = headers
            if collection == "fido2Methods":
                assert (
                    await client.get(path + "/" + collection, headers=headers)
                ).status_code == 403
                collection_headers = await headers_for(
                    database, settings, tenant, app, None, {"UserAuthMethod-Passkey.Read.All"}
                )
            response = await client.get(path + "/" + collection, headers=collection_headers)
            assert response.status_code == 200 and len(response.json()["value"]) == 1
        assert (await client.post(admin, json=descriptors[0])).status_code == 409
        assert (await client.get(admin)).status_code == 200
        delegated = await headers_for(
            database, settings, tenant, app, user, {"UserAuthenticationMethod.Read"}, "delegated"
        )
        assert (
            await client.get("/v1.0/me/authentication/methods", headers=delegated)
        ).status_code == 200
        assert (
            await client.get("/v1.0/me/authentication/methods", headers=headers)
        ).status_code == 400
        other = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "operator@contoso.test",
                "display_name": "Operator",
                "password": "local-password",
            },
        )
        assert other.status_code == 201, other.text
        other_id = other.json()["id"]
        operator = await headers_for(
            database,
            settings,
            tenant,
            app,
            other_id,
            {"UserAuthenticationMethod.Read.All"},
            "delegated",
        )
        assert (await client.get(path + "/methods", headers=operator)).status_code == 403
        role = f"/_mock/tenants/{tenant}/identity/users/{other_id}/roles/"
        assert (await client.put(role + "Authentication Administrator")).status_code == 200
        masked = await client.get(path + "/phoneMethods/" + MOBILE, headers=operator)
        assert masked.status_code == 200, masked.text
        assert masked.json()["phoneNumber"] != "+1 5555550123"
        assert masked.json()["phoneNumber"].endswith("0123")
        assert (
            await client.put(role + "Privileged Authentication Administrator")
        ).status_code == 200
        assert (await client.get(path + "/phoneMethods/" + MOBILE, headers=operator)).json()[
            "phoneNumber"
        ] == "+1 5555550123"
        assert (await client.get(path + "/methods?$select=id", headers=headers)).status_code == 400
        assert (
            await client.get(path + "/phoneMethods/" + PASSWORD, headers=headers)
        ).status_code == 404
        # Enrollment is durable state; removing it changes subsequent Graph requests.
        assert (await client.delete(admin + "/" + MOBILE)).status_code == 204
        assert (await client.get(path + "/phoneMethods", headers=headers)).json()["value"] == []


async def test_no_secret_disclosure(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        admin = f"/_mock/tenants/{tenant}/security/users/{user}/authenticationMethods"
        for field in ("password", "privateKey", "secret", "seed", "otpSeed", "verifier"):
            result = await client.post(
                admin,
                json={
                    "@odata.type": "#microsoft.graph.fido2AuthenticationMethod",
                    "displayName": "Invalid key",
                    field: "DO-NOT-STORE",
                },
            )
            assert result.status_code == 400, result.text
        assert (
            await client.post(
                admin,
                json={
                    "@odata.type": "#microsoft.graph.passwordAuthenticationMethod",
                    "id": PASSWORD,
                },
            )
        ).status_code == 400
        assert (
            await client.post(
                admin,
                json={
                    "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                    "phoneType": "alternateMobile",
                    "phoneNumber": "+1 5555550124",
                },
            )
        ).status_code == 400
        headers = await headers_for(
            database, settings, tenant, app, None, {"UserAuthenticationMethod.Read.All"}
        )
        response = await client.get(f"/v1.0/users/{user}/authentication/methods", headers=headers)
        assert response.status_code == 200 and len(response.json()["value"]) == 1
        assert "DO-NOT-STORE" not in response.text
        assert "local-directory-password" not in response.text
        denied = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        assert (
            await client.get(f"/v1.0/users/{user}/authentication/methods", headers=denied)
        ).status_code == 403


async def test_foreign_or_deleted_user_methods(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        headers = await headers_for(
            database, settings, tenant, app, None, {"UserAuthenticationMethod.Read.All"}
        )
        foreign_tenant = await client.post(
            "/_mock/tenants",
            json={
                "slug": "other-methods",
                "display_name": "Other methods tenant",
                "domain": "other-methods.test",
            },
        )
        assert foreign_tenant.status_code == 201, foreign_tenant.text
        foreign = await client.post(
            f"/_mock/tenants/{foreign_tenant.json()['id']}/identity/users",
            json={
                "upn": "foreign@other-methods.test",
                "display_name": "Foreign",
                "password": "local-password",
            },
        )
        assert foreign.status_code == 201, foreign.text
        for identifier in (foreign.json()["id"], str(uuid4())):
            assert (
                await client.get(
                    f"/v1.0/users/{identifier}/authentication/methods", headers=headers
                )
            ).status_code == 404
        by_upn = await client.get(
            "/v1.0/users/reader@contoso.test/authentication/passwordMethods", headers=headers
        )
        assert by_upn.status_code == 200, by_upn.text
        async with database.sessions.begin() as session:
            row = await session.get(User, UUID(str(user)))
            row.deleted_at = datetime.now(UTC)
        assert (
            await client.get(f"/v1.0/users/{user}/authentication/methods", headers=headers)
        ).status_code == 404
        assert (
            await client.post(
                f"/_mock/tenants/{tenant}/security/users/{user}/authenticationMethods",
                json={
                    "@odata.type": "#microsoft.graph.emailAuthenticationMethod",
                    "emailAddress": "hidden@example.test",
                },
            )
        ).status_code == 404


async def test_method_permissions_are_real_configurable_grants(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        result = await client.post(
            f"/_mock/tenants/{tenant}/identity/applications",
            json={
                "display_name": "Method reader",
                "client_mode": "public",
                "required_permissions": [
                    {
                        "resource": "graph",
                        "kind": "delegated",
                        "permissions": ["UserAuthenticationMethod.Read"],
                    }
                ],
            },
        )
        assert result.status_code == 201, result.text
        from mockazure.security.catalog import permission_id

        assert str(permission_id("UserAuthenticationMethod.Read", "delegated")) == (
            "1f6b61c5-2f65-4135-9c9f-31c0f8d32b52"
        )


async def test_granular_permissions_and_self_are_method_scoped(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        prefix = f"/v1.0/users/{user}/authentication/"
        for collection, family in (
            ("passwordMethods", "Password"),
            ("phoneMethods", "Phone"),
            ("emailMethods", "Email"),
            ("microsoftAuthenticatorMethods", "MicrosoftAuthApp"),
            ("fido2Methods", "Passkey"),
        ):
            headers = await headers_for(
                database, settings, tenant, app, None, {f"UserAuthMethod-{family}.Read.All"}
            )
            result = await client.get(prefix + collection, headers=headers)
            assert result.status_code == 200, result.text
            assert (await client.get(prefix + "methods", headers=headers)).status_code == 403
            headers = await headers_for(
                database,
                settings,
                tenant,
                app,
                user,
                {f"UserAuthMethod-{family}.Read"},
                "delegated",
            )
            assert (await client.get(prefix + collection, headers=headers)).status_code == 200
            assert (await client.get(prefix + "methods", headers=headers)).status_code == 403
        other = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "other@contoso.test",
                "display_name": "Other",
                "password": "local-password",
            },
        )
        assert other.status_code == 201
        self_headers = await headers_for(
            database, settings, tenant, app, user, {"UserAuthenticationMethod.Read"}, "delegated"
        )
        assert (
            await client.get(
                f"/v1.0/users/{other.json()['id']}/authentication/methods", headers=self_headers
            )
        ).status_code == 403


async def test_enrollment_validation_and_fresh_app_persistence(database, settings):
    import httpx

    from mockazure.admin.app import create_app

    async with graph_client(database, settings) as (client, tenant, app, user):
        admin = f"/_mock/tenants/{tenant}/security/users/{user}/authenticationMethods"
        for body in (
            {
                "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                "phoneType": "pager",
                "phoneNumber": "+1 5555550123",
            },
            {
                "@odata.type": "#microsoft.graph.fido2AuthenticationMethod",
                "displayName": "Key",
                "attestationLevel": "attested",
            },
            {
                "@odata.type": "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod",
                "displayName": "Phone",
                "createdDateTime": "1900-01-01T00:00:00Z",
            },
        ):
            assert (await client.post(admin, json=body)).status_code == 400
        created = await client.post(
            admin,
            json={
                "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                "phoneType": "mobile",
                "phoneNumber": "+1 5555550123",
            },
        )
        assert created.status_code == 201
        alternate = await client.post(
            admin,
            json={
                "@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                "phoneType": "alternateMobile",
                "phoneNumber": "+1 5555550124",
            },
        )
        assert alternate.status_code == 201
        assert (await client.delete(admin + "/" + MOBILE)).status_code == 400
        headers = await headers_for(
            database, settings, tenant, app, None, {"UserAuthenticationMethod.Read.All"}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as restarted:
            result = await restarted.get(
                f"/v1.0/users/{user}/authentication/phoneMethods", headers=headers
            )
            assert result.status_code == 200 and len(result.json()["value"]) == 2
            assert (await restarted.post(admin, json={})).status_code == 401
        assert (await client.delete(admin + "/" + alternate.json()["id"])).status_code == 204
        assert (await client.delete(admin + "/" + MOBILE)).status_code == 204
