from tests.integration.graph_support import graph_client, headers_for
from tests.integration.identity_support import identity_client
from tests.integration.test_identity_authorization import PASSWORD, hidden, params, provision
from tests.integration.test_password_reset import redeem

BASE = "/v1.0/policies/authenticationMethodsPolicy"
WRITE = {"Policy.ReadWrite.AuthenticationMethod"}
SMS = "3179e48a-750b-4051-897c-87b9720928f7"


def config(kind="Sms", **changes):
    name = "sms" if kind == "Sms" else "microsoftAuthenticator"
    return {
        "@odata.type": "#microsoft.graph." + name + "AuthenticationMethodConfiguration",
        **changes,
    }


async def enroll(client, tenant, user, kind="phone", **fields):
    value = {"@odata.type": "#microsoft.graph." + kind + "AuthenticationMethod", **fields}
    response = await client.post(
        f"/_mock/tenants/{tenant}/security/users/{user}/authenticationMethods", json=value
    )
    assert response.status_code == 201, response.text
    return response.json()


async def challenge(client, tenant, user, app):
    response = await client.get(
        f"/{tenant}/oauth2/v2.0/authorize",
        params=params(app, scope="openid User.Read offline_access"),
    )
    fields = {key: hidden(response, key) for key in ("transaction", "csrf")}
    assert (
        await client.post("/login", data=fields | {"username": user["upn"], "password": PASSWORD})
    ).status_code == 200
    response = await client.post("/consent", data=fields | {"decision": "approve"})
    assert response.status_code == 200, response.text
    return fields, response


async def test_policy_permissions_and_typed_reads(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        read = await headers_for(
            database, settings, tenant, app, None, {"Policy.Read.AuthenticationMethod"}
        )
        root = await client.get(BASE, headers=read)
        assert root.status_code == 200, root.text
        assert root.json()["id"] == "authenticationMethodsPolicy"
        assert "enforcement" not in root.text
        values = (
            await client.get(BASE + "/authenticationMethodConfigurations", headers=read)
        ).json()["value"]
        assert {v["id"] for v in values} == {"Sms", "MicrosoftAuthenticator"}
        assert (
            await client.patch(
                BASE + "/authenticationMethodConfigurations/sms",
                headers=read,
                json=config(state="disabled"),
            )
        ).status_code == 403
        wrong = await headers_for(
            database, settings, tenant, app, None, {"UserAuthenticationMethod.ReadWrite.All"}
        )
        assert (await client.get(BASE, headers=wrong)).status_code == 403
        delegated = await headers_for(database, settings, tenant, app, user, WRITE, "delegated")
        path = BASE + "/authenticationMethodConfigurations/Sms"
        assert (
            await client.patch(path, headers=delegated, json=config(state="disabled"))
        ).status_code == 403
        role = await client.put(
            f"/_mock/tenants/{tenant}/identity/users/{user}/roles/"
            "Authentication Policy Administrator"
        )
        assert role.status_code == 200, role.text
        assert (
            await client.patch(path, headers=delegated, json=config(state="disabled"))
        ).status_code == 204
        assert (await client.get(path, headers=read)).json()["state"] == "disabled"
        assert (await client.patch(BASE, headers=delegated, json={})).status_code == 501
        assert (await client.get(BASE.replace("v1.0", "beta"), headers=read)).status_code == 501


async def test_new_tenant_requires_registered_eligible_method(database, settings):
    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        *_,
    ):
        user, app = await provision(client, tenant)
        fields, page = await challenge(client, tenant, user, app)
        denied = await client.post(
            "/mfa",
            data=fields | {"decision": "complete", "method_id": SMS},
            headers={"X-MFA": "completed"},
        )
        assert denied.status_code == 400, denied.text
        await enroll(client, tenant, user["id"], "email", emailAddress="recovery@example.test")
        assert (
            await client.post("/mfa", data=fields | {"decision": "complete"})
        ).status_code == 400
        await enroll(client, tenant, user["id"], phoneType="mobile", phoneNumber="+1 5555550123")
        fields, page = await challenge(client, tenant, user, app)
        assert SMS in page.text
        callback = await client.post(
            "/mfa", data=fields | {"decision": "complete", "method_id": SMS}
        )
        assert callback.status_code == 303, callback.text
        assert (await redeem(client, tenant, app, callback)).status_code == 200


async def test_current_policy_and_deleted_method_block_stale_mfa(database, settings):
    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        control,
        *_,
    ):
        user, app = await provision(client, tenant)
        await enroll(client, tenant, user["id"], phoneType="mobile", phoneNumber="+1 5555550123")
        writer = await headers_for(database, settings, tenant, control, None, WRITE)
        path = BASE + "/authenticationMethodConfigurations/Sms"
        fields, _ = await challenge(client, tenant, user, app)
        assert (
            await client.patch(path, headers=writer, json=config(state="disabled"))
        ).status_code == 204
        assert (
            await client.post("/mfa", data=fields | {"decision": "complete", "method_id": SMS})
        ).status_code == 400
        assert (
            await client.patch(path, headers=writer, json=config(state="enabled"))
        ).status_code == 204
        callback = await client.post(
            "/mfa", data=fields | {"decision": "complete", "method_id": SMS}
        )
        tokens = await redeem(client, tenant, app, callback)
        assert tokens.status_code == 200, tokens.text
        assert (
            await client.patch(path, headers=writer, json=config(state="disabled"))
        ).status_code == 204
        refreshed = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": app["client_id"],
                "refresh_token": tokens.json()["refresh_token"],
            },
        )
        assert refreshed.status_code == 400, refreshed.text
        assert (
            await client.patch(path, headers=writer, json=config(state="enabled"))
        ).status_code == 204
        fields, _ = await challenge(client, tenant, user, app)
        callback = await client.post(
            "/mfa", data=fields | {"decision": "complete", "method_id": SMS}
        )
        mutation = await headers_for(
            database, settings, tenant, control, None, {"UserAuthenticationMethod.ReadWrite.All"}
        )
        assert (
            await client.delete(
                f"/v1.0/users/{user['id']}/authentication/phoneMethods/{SMS}", headers=mutation
            )
        ).status_code == 204
        assert (await redeem(client, tenant, app, callback)).status_code == 400


async def test_group_inclusions_exclusions_and_inventory_only_methods(database, settings):
    from mockazure.directory.models import DirectoryRelation
    from mockazure.security.method_policies import eligible_mfa_methods
    from tests.integration.graph_support import persisted_group

    async with graph_client(database, settings) as (client, tenant, app, user):
        await enroll(client, tenant, user, phoneType="mobile", phoneNumber="+1 5555550123")
        authenticator = await enroll(
            client, tenant, user, "microsoftAuthenticator", displayName="Lab"
        )
        await enroll(client, tenant, user, "email", emailAddress="a@example.test")
        async with database.sessions.begin() as session:
            group = await persisted_group(session, tenant)
            group_id = group.id
            session.add(
                DirectoryRelation(
                    tenant_id=tenant, group_id=group_id, object_id=user, relation="member"
                )
            )
        writer = await headers_for(database, settings, tenant, app, None, WRITE)
        path = BASE + "/authenticationMethodConfigurations/Sms"
        targets = [
            {
                "id": str(group_id),
                "targetType": "group",
                "isRegistrationRequired": False,
                "isUsableForSignIn": False,
            }
        ]
        assert (
            await client.patch(path, headers=writer, json=config(includeTargets=targets))
        ).status_code == 204
        async with database.sessions.begin() as session:
            assert set(await eligible_mfa_methods(session, tenant, user)) == {
                SMS,
                authenticator["id"],
            }
        assert (
            await client.patch(
                path,
                headers=writer,
                json=config(excludeTargets=[{"id": str(group_id), "targetType": "group"}]),
            )
        ).status_code == 204
        async with database.sessions.begin() as session:
            assert await eligible_mfa_methods(session, tenant, user) == (authenticator["id"],)
