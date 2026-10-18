from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from mockazure.security.models import SecurityObject
from tests.integration.graph_support import headers_for
from tests.integration.identity_support import identity_client
from tests.integration.intune_support import import_records, license_intune, record
from tests.integration.test_graph_licensing import P1, PLAN1, seed_sku
from tests.integration.test_identity_authorization import PASSWORD, hidden, params, provision
from tests.integration.test_method_policy_enforcement import (
    BASE,
    SMS,
    WRITE,
    challenge,
    config,
    enroll,
)
from tests.integration.test_password_reset import redeem
from tests.integration.test_policy_enforcement import add_policy
from tests.policy_support import policy_body


async def test_legacy_transition_is_atomic_without_fabricated_methods(database, settings):
    async with identity_client(database, settings) as (client, tenant, control, *_):
        user, app = await provision(client, tenant)
        profile = f"/_mock/tenants/{tenant}/identity/authentication-policy-profile"
        assert (await client.get(profile)).json()["method_policy_enforced"] is False
        writer = await headers_for(database, settings, tenant, control, None, WRITE)
        path = BASE + "/authenticationMethodConfigurations/Sms"
        assert (
            await client.patch(path, headers=writer, json=config(featureSettings={}))
        ).status_code == 400
        assert (await client.get(profile)).json()["method_policy_enforced"] is False
        assert (
            await client.patch(path, headers=writer, json=config(state="enabled"))
        ).status_code == 204
        assert (await client.get(profile)).json()["method_policy_enforced"] is True
        fields, page = await challenge(client, tenant, user, app)
        assert (
            await client.post("/mfa", data=fields | {"decision": "complete"})
        ).status_code == 400
        async with database.sessions() as session:
            methods = list(
                await session.scalars(
                    select(SecurityObject).where(
                        SecurityObject.tenant_id == UUID(tenant),
                        SecurityObject.kind == "authenticationMethod",
                    )
                )
            )
            assert methods == []


async def test_deleted_reenrolled_fixed_method_cannot_reuse_old_outcome(database, settings):
    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        control,
        *_,
    ):
        user, app = await provision(client, tenant)
        await enroll(client, tenant, user["id"], phoneType="mobile", phoneNumber="+1 5555550123")
        fields, _ = await challenge(client, tenant, user, app)
        callback = await client.post(
            "/mfa", data=fields | {"decision": "complete", "method_id": SMS}
        )
        writer = await headers_for(
            database, settings, tenant, control, None, {"UserAuthenticationMethod.ReadWrite.All"}
        )
        assert (
            await client.delete(
                f"/v1.0/users/{user['id']}/authentication/phoneMethods/{SMS}", headers=writer
            )
        ).status_code == 204
        await enroll(client, tenant, user["id"], phoneType="mobile", phoneNumber="+1 5555550123")
        assert (await redeem(client, tenant, app, callback)).status_code == 400


async def bound_login(client, tenant, user, app, device_id, *, mfa="skip"):
    begin = await client.get(
        f"/{tenant}/oauth2/v2.0/authorize",
        params=params(app, scope="openid User.Read offline_access"),
    )
    fields = {key: hidden(begin, key) for key in ("transaction", "csrf")}
    binding = await client.put(
        f"/_mock/tenants/{tenant}/identity/sessions/{fields['transaction']}/authentication-context",
        json={"device_id": str(device_id), "platform": "windows"},
    )
    assert binding.status_code == 200, binding.text
    for path, extra in (
        ("/login", {"username": user["upn"], "password": PASSWORD}),
        ("/consent", {"decision": "approve"}),
        ("/mfa", {"decision": mfa, "method_id": SMS}),
    ):
        response = await client.post(path, data=fields | extra)
        if response.status_code != 200:
            return response
    return response


@pytest.mark.parametrize(
    "state,operator,mfa,allowed",
    [
        ("compliant", "OR", "skip", True),
        ("compliant", "AND", "skip", False),
        ("compliant", "AND", "complete", True),
        ("unknown", "OR", "skip", False),
        ("unknown", "OR", "complete", True),
        ("unknown", "AND", "complete", False),
        ("noncompliant", "OR", "complete", True),
        ("noncompliant", "AND", "complete", False),
    ],
)
async def test_device_observation_controls(database, settings, state, operator, mfa, allowed):
    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        control,
        *_,
    ):
        user, app = await provision(client, tenant)
        await seed_sku(client, tenant, P1, PLAN1)
        await license_intune(client, tenant)
        await enroll(client, tenant, user["id"], phoneType="mobile", phoneNumber="+1 5555550123")
        device_id, object_id = uuid4(), uuid4()
        async with database.sessions.begin() as session:
            session.add(
                SecurityObject(
                    tenant_id=UUID(tenant),
                    id=object_id,
                    kind="device",
                    properties={
                        "deviceId": str(device_id),
                        "accountEnabled": True,
                        "operatingSystem": "Windows",
                        "displayName": "Local endpoint",
                    },
                )
            )
        result = await import_records(
            client,
            tenant,
            [
                record(
                    "managedDevice",
                    "managed",
                    azureADDeviceId=str(device_id),
                    complianceState=state,
                )
            ],
        )
        assert result.status_code == 200, result.text
        writer = await headers_for(
            database,
            settings,
            tenant,
            control,
            None,
            {"Policy.Read.All", "Policy.ReadWrite.ConditionalAccess"},
        )
        body = policy_body()
        body["grantControls"] = {
            "operator": operator,
            "builtInControls": ["mfa", "compliantDevice"],
        }
        await add_policy(client, writer, body)
        result = await bound_login(client, tenant, user, app, device_id, mfa=mfa)
        assert result.status_code == (303 if allowed else 400), result.text
        if allowed:
            assert (await redeem(client, tenant, app, result)).status_code == 200


async def test_role_platform_targets_and_device_revocation(database, settings):
    from tests.integration.test_policy_enforcement import authenticate

    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        control,
        *_,
    ):
        user, app = await provision(client, tenant)
        await seed_sku(client, tenant, P1, PLAN1)
        await license_intune(client, tenant)
        role_id = "f2ef992c-3afb-46b9-b7cf-a126ee74c451"
        assert (
            await client.put(
                f"/_mock/tenants/{tenant}/identity/users/{user['id']}/roles/Global Reader"
            )
        ).status_code == 200
        device_id, object_id = uuid4(), uuid4()
        async with database.sessions.begin() as session:
            session.add(
                SecurityObject(
                    tenant_id=UUID(tenant),
                    id=object_id,
                    kind="device",
                    properties={
                        "deviceId": str(device_id),
                        "accountEnabled": True,
                        "operatingSystem": "Windows",
                        "displayName": "Local endpoint",
                    },
                )
            )
        result = await import_records(
            client,
            tenant,
            [
                record(
                    "managedDevice",
                    "managed",
                    azureADDeviceId=str(device_id),
                    complianceState="compliant",
                )
            ],
        )
        assert result.status_code == 200, result.text
        writer = await headers_for(
            database,
            settings,
            tenant,
            control,
            None,
            {"Policy.Read.All", "Policy.ReadWrite.ConditionalAccess"},
        )
        body = policy_body(control="block")
        body["conditions"]["users"] = {"includeRoles": [role_id]}
        body["conditions"]["platforms"] = {"includePlatforms": ["windows"]}
        path = await add_policy(client, writer, body)
        # A header cannot make an unbound device match a Windows policy.
        assert (
            await authenticate(
                client,
                tenant,
                app,
                request_headers={"X-Device-Platform": "windows", "X-Device-Id": str(device_id)},
            )
        ).status_code == 303
        assert (await bound_login(client, tenant, user, app, device_id)).status_code == 400
        assert (
            await client.patch(
                path, headers=writer, json={"conditions": {"users": {"excludeRoles": [role_id]}}}
            )
        ).status_code == 204
        assert (await bound_login(client, tenant, user, app, device_id)).status_code == 303
        assert (
            await client.patch(
                path,
                headers=writer,
                json={
                    "conditions": {
                        "users": {"excludeRoles": []},
                        "platforms": {"excludePlatforms": ["windows"]},
                    }
                },
            )
        ).status_code == 204
        assert (await bound_login(client, tenant, user, app, device_id)).status_code == 303
        assert (
            await client.patch(
                path,
                headers=writer,
                json={
                    "conditions": {
                        "platforms": {"includePlatforms": ["all"], "excludePlatforms": []}
                    },
                    "grantControls": {"operator": "AND", "builtInControls": ["compliantDevice"]},
                },
            )
        ).status_code == 204
        tokens = await redeem(
            client, tenant, app, await bound_login(client, tenant, user, app, device_id)
        )
        assert tokens.status_code == 200, tokens.text
        refresh = {
            "grant_type": "refresh_token",
            "client_id": app["client_id"],
            "refresh_token": tokens.json()["refresh_token"],
        }
        device_auth = await headers_for(
            database, settings, tenant, control, None, {"Device.ReadWrite.All"}
        )
        assert (
            await client.patch(
                f"/v1.0/devices/{object_id}", headers=device_auth, json={"accountEnabled": False}
            )
        ).status_code == 204
        assert (await client.post(f"/{tenant}/oauth2/v2.0/token", data=refresh)).status_code == 400
        assert (
            await client.patch(
                f"/v1.0/devices/{object_id}", headers=device_auth, json={"accountEnabled": True}
            )
        ).status_code == 204
        assert (await client.post(f"/{tenant}/oauth2/v2.0/token", data=refresh)).status_code == 200
        assert (
            await client.delete(f"/v1.0/devices/{object_id}", headers=device_auth)
        ).status_code == 204
        assert (await client.post(f"/{tenant}/oauth2/v2.0/token", data=refresh)).status_code == 400


async def test_context_binding_is_admin_only_and_rejects_mfa_assertions(database, settings):
    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        control,
        *_,
    ):
        user, app = await provision(client, tenant)
        begin = await client.get(f"/{tenant}/oauth2/v2.0/authorize", params=params(app))
        identifier = hidden(begin, "transaction")
        path = f"/_mock/tenants/{tenant}/identity/sessions/{identifier}/authentication-context"
        device_id = uuid4()
        async with database.sessions.begin() as session:
            session.add(
                SecurityObject(
                    tenant_id=UUID(tenant),
                    id=uuid4(),
                    kind="device",
                    properties={
                        "deviceId": str(device_id),
                        "accountEnabled": True,
                        "operatingSystem": "Windows",
                        "displayName": "Local endpoint",
                    },
                )
            )
        caller = await headers_for(
            database, settings, tenant, control, user["id"], {"User.Read"}, "delegated"
        )
        body = {"device_id": str(device_id), "platform": "windows"}
        assert (await client.put(path, headers=caller, json=body)).status_code in {401, 403}
        assert (
            await client.put(path, json=body | {"method_id": SMS, "mfa": "completed"})
        ).status_code == 422
        assert (
            await client.put(path.replace(identifier, str(uuid4())), json=body)
        ).status_code == 404
        assert (await client.put(path, json=body)).status_code == 200
        context = (await client.get(path)).json()
        assert context["method_id"] is None and context["method_completed_at"] is None


async def test_registered_mfa_survives_device_code_and_api_restart(database, settings):
    import httpx

    from mockazure.admin.app import create_app

    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        control,
        *_,
    ):
        user, app = await provision(client, tenant)
        await enroll(client, tenant, user["id"], phoneType="mobile", phoneNumber="+1 5555550123")
        endpoint = f"/{tenant}/oauth2/v2.0"
        started = await client.post(
            endpoint + "/devicecode",
            data={"client_id": app["client_id"], "scope": "User.Read offline_access"},
        )
        assert started.status_code == 200, started.text
        device = started.json()
        response = await client.get("/device", params={"user_code": device["user_code"]})
        fields = {key: hidden(response, key) for key in ("transaction", "csrf")}
        assert (
            await client.post(
                "/login", data=fields | {"username": user["upn"], "password": PASSWORD}
            )
        ).status_code == 200
        assert (
            await client.post("/consent", data=fields | {"decision": "approve"})
        ).status_code == 200
        approved = await client.post(
            "/mfa", data=fields | {"decision": "complete", "method_id": SMS}
        )
        assert approved.status_code == 200, approved.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(settings, database)),
            base_url="https://localhost:8443",
        ) as replica:
            issued = await replica.post(
                endpoint + "/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": app["client_id"],
                    "device_code": device["device_code"],
                },
            )
            assert issued.status_code == 200, issued.text
            refreshed = await replica.post(
                endpoint + "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": app["client_id"],
                    "refresh_token": issued.json()["refresh_token"],
                },
            )
            assert refreshed.status_code == 200, refreshed.text
