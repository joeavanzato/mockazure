from datetime import UTC, datetime, timedelta
from uuid import uuid4

from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
from mockazure.identity.tokens import issue_access_token
from mockazure.identity.types import PrincipalContext
from mockazure.oauth.models import LoginSession
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_method_management import EMAIL, MOBILE, WRITE
from tests.integration.test_security_report_boundaries import seed_sku


async def test_self_delete_requires_current_recent_bound_session(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        app_auth = await headers_for(database, settings, tenant, app, None, WRITE)
        path = f"/v1.0/users/{user}/authentication/emailMethods"
        assert (
            await client.post(path, json={"emailAddress": "recover@example.test"}, headers=app_auth)
        ).status_code == 201
        own_path = "/v1.0/me/authentication/emailMethods/" + EMAIL
        own = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"UserAuthenticationMethod.ReadWrite"},
            "delegated",
        )
        assert (
            await client.delete(own_path, headers={**own, "X-MFA": "completed"})
        ).status_code == 403
        now, identifier = datetime.now(UTC), uuid4()
        async with database.sessions.begin() as session:
            session.add(
                LoginSession(
                    id=identifier,
                    tenant_id=tenant,
                    client_id=app,
                    user_id=user,
                    resource="graph",
                    scopes=["UserAuthenticationMethod.ReadWrite"],
                    mfa="not_performed",
                    created_at=now - timedelta(minutes=11),
                    expires_at=now + timedelta(hours=1),
                )
            )
        actor = PrincipalContext(
            tenant,
            app,
            user,
            "delegated",
            "00000003-0000-0000-c000-000000000000",
            frozenset({"UserAuthenticationMethod.ReadWrite"}),
            identifier,
        )
        async with database.sessions() as session:
            raw = await issue_access_token(
                actor,
                now,
                SigningKeyStore(session, read_wrapping_key(settings.data_dir), settings.endpoints),
            )
        auth = {"Authorization": "Bearer " + raw}
        assert (await client.delete(own_path, headers=auth)).status_code == 403
        async with database.sessions.begin() as session:
            login = await session.get(LoginSession, identifier)
            login.created_at = now
        assert (await client.delete(own_path, headers=auth)).status_code == 204


async def test_registration_and_tenant_method_boundaries(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        auth = await headers_for(
            database, settings, tenant, app, None, WRITE | {"AuditLog.Read.All"}
        )
        path = f"/v1.0/users/{user}/authentication/phoneMethods"
        created = await client.post(
            path, json={"phoneType": "mobile", "phoneNumber": "+1 5555550123"}, headers=auth
        )
        assert created.status_code == 201
        report = "/v1.0/reports/authenticationMethods/userRegistrationDetails/" + str(user)
        current = await client.get(report, headers=auth)
        assert (
            current.status_code == 200 and "mobilePhone" in current.json()["methodsRegistered"]
        ), current.text
        assert (await client.delete(path + "/" + MOBILE, headers=auth)).status_code == 204
        assert (
            "mobilePhone"
            not in (await client.get(report, headers=auth)).json()["methodsRegistered"]
        )
        denied = await headers_for(
            database, settings, tenant, app, None, {"UserAuthMethod-Email.ReadWrite.All"}
        )
        assert (
            await client.post(
                path, json={"phoneType": "mobile", "phoneNumber": "+1 5555550123"}, headers=denied
            )
        ).status_code == 403
        assert (
            await client.post(path.replace(str(user), str(uuid4())), json={}, headers=auth)
        ).status_code == 404
