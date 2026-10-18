"""Final-review regressions for current authorization state and linked flow lifetimes."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from mockazure.directory.models import DirectoryRelation
from mockazure.directory.mutations import AdminActor, DirectoryCommand, mutate_directory
from mockazure.licensing.schemas import SkuInput
from mockazure.licensing.service import upsert_sku
from mockazure.oauth.device_models import DeviceAuthorization
from mockazure.oauth.sessions import cleanup_expired, digest
from mockazure.security.authentication_models import AuthenticationContext
from mockazure.security.models import SecurityObject
from tests.integration.graph_support import headers_for, persisted_group
from tests.integration.identity_support import identity_client
from tests.integration.intune_support import (
    INTUNE_PLAN,
    INTUNE_SKU,
    import_records,
    license_intune,
    record,
)
from tests.integration.test_authentication_policy_boundaries import bound_login
from tests.integration.test_graph_licensing import P1, PLAN1, seed_sku
from tests.integration.test_identity_authorization import PASSWORD, hidden, provision
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


async def device_fixture(client, database, tenant):
    await seed_sku(client, tenant, P1, PLAN1)
    await license_intune(client, tenant)
    device = uuid4()
    async with database.sessions.begin() as session:
        session.add(
            SecurityObject(
                tenant_id=UUID(tenant),
                id=uuid4(),
                kind="device",
                properties={
                    "deviceId": str(device),
                    "accountEnabled": True,
                    "operatingSystem": "Windows",
                    "displayName": "Review endpoint",
                },
            )
        )
    response = await import_records(
        client,
        tenant,
        [
            record(
                "managedDevice",
                "review-device",
                azureADDeviceId=str(device),
                complianceState="compliant",
            )
        ],
    )
    assert response.status_code == 200, response.text
    return device


@pytest.mark.parametrize(
    "change", ["remove_member", "add_exclusion", "disable_user", "revoke_intune"]
)
async def test_revocation_cannot_commit_between_final_evaluation_and_issuance(
    database, settings, monkeypatch, change
):
    from mockazure.security import evaluation

    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        control,
        *_,
    ):
        user, app = await provision(client, tenant)
        await enroll(client, tenant, user["id"], phoneType="mobile", phoneNumber="+1 5555550123")
        device = await device_fixture(client, database, tenant)
        async with database.sessions.begin() as session:
            included = (await persisted_group(session, UUID(tenant))).id
            excluded = (await persisted_group(session, UUID(tenant))).id
            session.add(
                DirectoryRelation(
                    tenant_id=UUID(tenant),
                    group_id=included,
                    object_id=UUID(user["id"]),
                    relation="member",
                )
            )
        writer = await headers_for(
            database,
            settings,
            tenant,
            control,
            None,
            WRITE | {"Policy.Read.All", "Policy.ReadWrite.ConditionalAccess"},
        )
        response = await client.patch(
            BASE + "/authenticationMethodConfigurations/Sms",
            headers=writer,
            json=config(
                includeTargets=[{"id": str(included), "targetType": "group"}],
                excludeTargets=[{"id": str(excluded), "targetType": "group"}],
            ),
        )
        assert response.status_code == 204, response.text
        body = policy_body()
        body["grantControls"] = {"operator": "AND", "builtInControls": ["mfa", "compliantDevice"]}
        await add_policy(client, writer, body)
        callback = await bound_login(client, tenant, user, app, device, mfa="complete")
        assert callback.status_code == 303, callback.text
        evaluated, release, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = evaluation.evaluate_policies

        async def pause_after_evaluation(*args, **kwargs):
            result = await original(*args, **kwargs)
            evaluated.set()
            await asyncio.wait_for(release.wait(), 10)
            return result

        monkeypatch.setattr(evaluation, "evaluate_policies", pause_after_evaluation)
        issuance = asyncio.create_task(redeem(client, tenant, app, callback))
        await asyncio.wait_for(evaluated.wait(), 10)
        writer_pid = []

        async def revoke():
            async with database.sessions.begin() as session:
                writer_pid.append(await session.scalar(text("select pg_backend_pid()")))
                entered.set()
                if change == "revoke_intune":
                    await upsert_sku(
                        session,
                        UUID(tenant),
                        SkuInput(
                            sku_id=UUID(INTUNE_SKU),
                            sku_part_number="INTUNE_A",
                            status="Suspended",
                            enabled=10,
                            service_plans=[
                                {
                                    "servicePlanId": INTUNE_PLAN,
                                    "servicePlanName": "INTUNE_A",
                                    "provisioningStatus": "Success",
                                }
                            ],
                        ),
                    )
                else:
                    command = (
                        DirectoryCommand(
                            "user", "update", UUID(user["id"]), {"accountEnabled": False}
                        )
                        if change == "disable_user"
                        else DirectoryCommand(
                            "group",
                            "add_member" if change == "add_exclusion" else "remove_member",
                            excluded if change == "add_exclusion" else included,
                            {"member_id": UUID(user["id"])},
                        )
                    )
                    await mutate_directory(
                        session, UUID(tenant), command, AdminActor(), datetime.now(UTC)
                    )

        mutation = asyncio.create_task(revoke())
        blocked = False
        try:
            await asyncio.wait_for(entered.wait(), 10)
            for _ in range(200):
                if mutation.done():
                    break
                async with database.sessions() as observer:
                    state = await observer.scalar(
                        text("select wait_event_type from pg_stat_activity where pid=:pid"),
                        {"pid": writer_pid[0]},
                    )
                if state == "Lock":
                    blocked = True
                    break
                await asyncio.sleep(0.01)
        finally:
            release.set()
            issued, _ = await asyncio.gather(issuance, mutation)
        assert blocked, "Authorization state changed after evaluation but before token commit"
        assert issued.status_code == 200, issued.text
        refreshed = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "refresh_token",
                "client_id": app["client_id"],
                "refresh_token": issued.json()["refresh_token"],
            },
        )
        assert refreshed.status_code == 400, refreshed.text


@pytest.mark.parametrize("binding", ["device_before_browser", "device_after_browser", "browser"])
@pytest.mark.parametrize("control_name", ["block", "compliantDevice"])
async def test_device_code_binding_is_authoritative_during_approval(
    database, settings, binding, control_name
):
    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        control,
        *_,
    ):
        user, app = await provision(client, tenant)
        await enroll(client, tenant, user["id"], phoneType="mobile", phoneNumber="+1 5555550123")
        device = await device_fixture(client, database, tenant)
        writer = await headers_for(
            database,
            settings,
            tenant,
            control,
            None,
            {"Policy.Read.All", "Policy.ReadWrite.ConditionalAccess"},
        )
        body = policy_body(control=control_name)
        body["conditions"]["platforms"] = {"includePlatforms": ["windows"]}
        await add_policy(client, writer, body)
        endpoint = f"/{tenant}/oauth2/v2.0"
        response = await client.post(
            endpoint + "/devicecode",
            data={"client_id": app["client_id"], "scope": "User.Read offline_access"},
        )
        assert response.status_code == 200, response.text
        started = response.json()
        async with database.sessions() as session:
            identifier = await session.scalar(
                select(DeviceAuthorization.id).where(
                    DeviceAuthorization.digest == digest(started["device_code"])
                )
            )

        async def bind(key):
            result = await client.put(
                f"/_mock/tenants/{tenant}/identity/sessions/{key}/authentication-context",
                json={"device_id": str(device), "platform": "windows"},
            )
            assert result.status_code == 200, result.text

        if binding == "device_before_browser":
            await bind(identifier)
        response = await client.get("/device", params={"user_code": started["user_code"]})
        fields = {key: hidden(response, key) for key in ("transaction", "csrf")}
        if binding != "device_before_browser":
            await bind(identifier if binding == "device_after_browser" else fields["transaction"])
        for path, data in (
            ("/login", {"username": user["upn"], "password": PASSWORD}),
            ("/consent", {"decision": "approve"}),
            ("/mfa", {"decision": "complete", "method_id": SMS}),
        ):
            response = await client.post(path, data=fields | data)
            if response.status_code != 200:
                break
        if control_name == "block":
            assert response.status_code == 400, response.text
            assert response.json()["error"] == "access_denied"
            return
        assert response.status_code == 200, response.text
        async with database.sessions() as session:
            context = await session.get(AuthenticationContext, identifier)
            assert context.device_id == device and context.platform == "windows"
        token = await client.post(
            endpoint + "/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": app["client_id"],
                "device_code": started["device_code"],
            },
        )
        assert token.status_code == 200, token.text


async def test_completed_mfa_survives_until_code_expiry_and_cleanup(database, settings):
    async with identity_client(database, settings, legacy_method_policy=False) as (
        client,
        tenant,
        _,
        *rest,
    ):
        now = datetime.now(UTC)
        client._transport.app.state.clock = lambda: now
        user, app = await provision(client, tenant)
        await enroll(client, tenant, user["id"], phoneType="mobile", phoneNumber="+1 5555550123")
        fields, _ = await challenge(client, tenant, user, app)
        now += timedelta(minutes=14, seconds=59)
        callback = await client.post(
            "/mfa", data=fields | {"decision": "complete", "method_id": SMS}
        )
        assert callback.status_code == 303, callback.text
        now += timedelta(seconds=2)
        async with database.sessions.begin() as session:
            await cleanup_expired(session, UUID(tenant), now)
            context = await session.get(AuthenticationContext, UUID(fields["transaction"]))
            assert context is not None, "Cleanup removed proof for an unexpired authorization code"
        token = await redeem(client, tenant, app, callback)
        assert token.status_code == 200, token.text
        now += timedelta(minutes=5)
        async with database.sessions.begin() as session:
            await cleanup_expired(session, UUID(tenant), now)
            assert await session.get(AuthenticationContext, UUID(fields["transaction"])) is None
