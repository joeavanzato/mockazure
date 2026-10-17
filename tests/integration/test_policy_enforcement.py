"""Policy changes must alter real local OAuth issuance and immutable sign-in facts."""

from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx

from mockazure.admin.app import create_app
from tests.integration.graph_support import headers_for
from tests.integration.identity_support import identity_client
from tests.integration.test_evidence_operations import drain_api
from tests.integration.test_graph_licensing import P1, PLAN1, seed_sku
from tests.integration.test_identity_authorization import (
    CALLBACK,
    PASSWORD,
    VERIFIER,
    hidden,
    params,
    provision,
)
from tests.integration.test_identity_sessions import browser_device
from tests.integration.test_security_policies import BASE, WRITE
from tests.policy_support import location_body, policy_body


@asynccontextmanager
async def policy_client(database, settings):
    async with identity_client(database, settings) as (client, tenant, control_app, *_):
        user, app = await provision(client, tenant)
        await seed_sku(client, tenant, P1, PLAN1)
        assert (
            await client.patch(
                f"/_mock/tenants/{tenant}/evidence/settings", json={"delay_seconds": 0}
            )
        ).status_code == 200
        writer = await headers_for(database, settings, tenant, control_app, None, WRITE)
        yield client, tenant, control_app, user, app, writer


async def add_policy(client, headers, body):
    response = await client.post(BASE + "/policies", headers=headers, json=body)
    assert response.status_code == 201, response.text
    return BASE + "/policies/" + response.json()["id"]


async def authenticate(client, tenant, app, *, mfa="skip", request_headers=None):
    response = await client.get(
        f"/{tenant}/oauth2/v2.0/authorize",
        params=params(app, scope="openid User.Read offline_access"),
        headers=request_headers,
    )
    assert response.status_code == 200, response.text
    fields = {key: hidden(response, key) for key in ("transaction", "csrf")}
    for path, data in (
        ("/login", {"username": "reader@contoso.test", "password": PASSWORD}),
        ("/consent", {"decision": "approve"}),
        ("/mfa", {"decision": mfa}),
    ):
        response = await client.post(path, data=fields | data, headers=request_headers)
        if response.status_code != 200:
            break
    return response


async def redeem(client, tenant, app, callback):
    assert callback.status_code == 303, callback.text
    code = parse_qs(urlsplit(callback.headers["location"]).query)["code"][0]
    return await client.post(
        f"/{tenant}/oauth2/v2.0/token",
        data={
            "grant_type": "authorization_code",
            "client_id": app["client_id"],
            "redirect_uri": CALLBACK,
            "code_verifier": VERIFIER,
            "code": code,
        },
    )


async def test_block_mfa_report_only(database, settings):
    async with policy_client(database, settings) as (client, tenant, _, user, app, writer):
        path = await add_policy(client, writer, policy_body(control="block"))
        denied = await authenticate(client, tenant, app)
        assert denied.status_code == 400, denied.text
        assert denied.json()["error"] == "access_denied"
        assert "access_token" not in denied.text
        assert (
            await client.patch(
                path,
                headers=writer,
                json={"grantControls": {"operator": "OR", "builtInControls": ["mfa"]}},
            )
        ).status_code == 204
        assert (await authenticate(client, tenant, app)).status_code == 400
        success = await redeem(
            client, tenant, app, await authenticate(client, tenant, app, mfa="complete")
        )
        assert success.status_code == 200, success.text
        assert (
            await client.patch(
                path,
                headers=writer,
                json={
                    "state": "enabledForReportingButNotEnforced",
                    "grantControls": {"operator": "OR", "builtInControls": ["block"]},
                },
            )
        ).status_code == 204
        assert (
            await redeem(client, tenant, app, await authenticate(client, tenant, app))
        ).status_code == 200
        assert (
            await client.patch(path, headers=writer, json={"state": "disabled"})
        ).status_code == 204
        assert (
            await redeem(client, tenant, app, await authenticate(client, tenant, app))
        ).status_code == 200


async def test_policy_target_exclusions_and_ipv4_ipv6(database, settings):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        directory = await headers_for(
            database, settings, tenant, control, None, {"Group.ReadWrite.All"}
        )
        group = await client.post(
            "/v1.0/groups",
            headers=directory,
            json={
                "displayName": "Policy target",
                "mailEnabled": False,
                "mailNickname": "policy-target",
                "securityEnabled": True,
            },
        )
        assert group.status_code == 201, group.text
        assert (
            await client.post(
                f"/v1.0/groups/{group.json()['id']}/members/$ref",
                headers=directory,
                json={"@odata.id": f"https://localhost:8443/v1.0/directoryObjects/{user['id']}"},
            )
        ).status_code == 204
        location = await client.post(BASE + "/namedLocations", headers=writer, json=location_body())
        assert location.status_code == 201
        body = policy_body(control="block")
        body["conditions"]["users"] = {
            "includeGroups": [group.json()["id"]],
            "excludeUsers": [user["id"]],
        }
        body["conditions"]["applications"] = {
            "includeApplications": ["00000003-0000-0000-c000-000000000000"]
        }
        body["conditions"]["locations"] = {
            "includeLocations": ["All"],
            "excludeLocations": ["AllTrusted"],
        }
        path = await add_policy(client, writer, body)
        assert (await authenticate(client, tenant, app)).status_code == 303
        assert (
            await client.patch(
                path, headers=writer, json={"conditions": {"users": {"excludeUsers": []}}}
            )
        ).status_code == 204
        assert (
            await authenticate(client, tenant, app)
        ).status_code == 303  # Trusted peer excluded.
        assert (
            await client.patch(
                BASE + "/namedLocations/" + location.json()["id"],
                headers=writer,
                json={"isTrusted": False},
            )
        ).status_code == 204
        assert (
            await authenticate(
                client,
                tenant,
                app,
                request_headers={"X-Forwarded-For": "203.0.113.9", "Forwarded": "for=203.0.113.9"},
            )
        ).status_code == 400
        assert (
            await client.patch(
                path,
                headers=writer,
                json={
                    "conditions": {
                        "locations": {
                            "includeLocations": [location.json()["id"]],
                            "excludeLocations": [],
                        }
                    }
                },
            )
        ).status_code == 204
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_app(settings, database), client=("::1", 12345)
            ),
            base_url="https://localhost:8443",
        ) as ipv6:
            assert (await authenticate(ipv6, tenant, app)).status_code == 400
        assert (
            await client.patch(
                path,
                headers=writer,
                json={
                    "conditions": {
                        "applications": {
                            "excludeApplications": ["00000003-0000-0000-c000-000000000000"]
                        }
                    }
                },
            )
        ).status_code == 204
        assert (await authenticate(client, tenant, app)).status_code == 303
        assert (
            await client.patch(
                path,
                headers=writer,
                json={
                    "conditions": {
                        "applications": {"excludeApplications": []},
                        "users": {"excludeGroups": [group.json()["id"]]},
                    }
                },
            )
        ).status_code == 204
        assert (await authenticate(client, tenant, app)).status_code == 303


async def test_policy_change_before_redeem_refresh_and_device(database, settings):
    async with policy_client(database, settings) as (client, tenant, _, user, app, writer):
        callback = await authenticate(client, tenant, app)
        path = await add_policy(client, writer, policy_body(control="block"))
        assert (await redeem(client, tenant, app, callback)).status_code == 400
        assert (
            await client.patch(path, headers=writer, json={"state": "disabled"})
        ).status_code == 204
        tokens = await redeem(client, tenant, app, await authenticate(client, tenant, app))
        assert tokens.status_code == 200
        refresh = {
            "grant_type": "refresh_token",
            "client_id": app["client_id"],
            "refresh_token": tokens.json()["refresh_token"],
        }
        assert (
            await client.patch(
                path,
                headers=writer,
                json={
                    "state": "enabled",
                    "grantControls": {"operator": "OR", "builtInControls": ["mfa"]},
                },
            )
        ).status_code == 204
        response = await client.post(f"/{tenant}/oauth2/v2.0/token", data=refresh)
        assert response.status_code == 400 and "access_token" not in response.json()
        assert (
            await client.patch(path, headers=writer, json={"state": "disabled"})
        ).status_code == 204
        device = await client.post(
            f"/{tenant}/oauth2/v2.0/devicecode",
            data={
                "client_id": app["client_id"],
                "scope": "User.Read offline_access",
            },
        )
        assert device.status_code == 200
        await browser_device(client, device.json())
        assert (
            await client.patch(
                path,
                headers=writer,
                json={
                    "state": "enabled",
                    "grantControls": {"operator": "OR", "builtInControls": ["block"]},
                },
            )
        ).status_code == 204
        blocked = await client.post(
            f"/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": app["client_id"],
                "device_code": device.json()["device_code"],
            },
        )
        assert blocked.status_code == 400 and "access_token" not in blocked.json()
        assert (await client.post(f"/{tenant}/oauth2/v2.0/token", data=refresh)).status_code == 400


async def test_ca_evidence_and_denied_disclosure(database, settings):
    async with policy_client(database, settings) as (client, tenant, control, user, app, writer):
        body = policy_body(state="enabledForReportingButNotEnforced", control="block")
        body["displayName"] = "Original report-only policy"
        path = await add_policy(client, writer, body)
        success = await redeem(client, tenant, app, await authenticate(client, tenant, app))
        assert success.status_code == 200
        assert (
            await client.patch(
                path,
                headers=writer,
                json={"displayName": "Renamed enforced policy", "state": "enabled"},
            )
        ).status_code == 204
        assert (await authenticate(client, tenant, app)).status_code == 400
        await drain_api(client, tenant)
        reader = await headers_for(database, settings, tenant, control, None, {"AuditLog.Read.All"})
        url = "/v1.0/auditLogs/signIns"
        masked = await client.get(url, headers=reader)
        assert masked.status_code == 200, masked.text
        assert len(masked.json()["value"]) == 2
        assert all("appliedConditionalAccessPolicies" not in v for v in masked.json()["value"])
        details = await headers_for(
            database,
            settings,
            tenant,
            control,
            None,
            {
                "AuditLog.Read.All",
                "Policy.Read.ConditionalAccess",
            },
        )
        detail_headers = details | {"Prefer": "include-unknown-enum-members"}
        detailed = await client.get(url, headers=detail_headers)
        assert detailed.status_code == 200, detailed.text
        records = detailed.json()["value"]
        successful = next(v for v in records if v["status"]["errorCode"] == 0)
        failed = next(v for v in records if v["status"]["errorCode"] != 0)
        assert successful["conditionalAccessStatus"] == "notApplied"
        assert (
            successful["appliedConditionalAccessPolicies"][0]["displayName"]
            == "Original report-only policy"
        )
        assert successful["appliedConditionalAccessPolicies"][0]["result"] == "reportOnlyFailure"
        assert failed["status"]["errorCode"] == 53003
        assert failed["conditionalAccessStatus"] == "failure"
        assert failed["appliedConditionalAccessPolicies"][0]["result"] == "failure"
        assert failed["ipAddress"] == "127.0.0.1"
        legacy_enums = await client.get(url, headers=details)
        old_success = next(v for v in legacy_enums.json()["value"] if v["status"]["errorCode"] == 0)
        assert old_success["appliedConditionalAccessPolicies"][0]["result"] == "unknownFutureValue"
        exported = await client.get(f"/_mock/tenants/{tenant}/evidence/export?family=signIn")
        assert exported.status_code == 200, exported.text
        assert len(exported.json()["records"]) == 2
        # The official sign-in list contract does not support $select.
        selected = await client.get(
            url + "?$select=id,appliedConditionalAccessPolicies", headers=reader
        )
        assert selected.status_code == 400
        item = await client.get(url + "/" + successful["id"], headers=reader)
        assert item.status_code == 200 and "appliedConditionalAccessPolicies" not in item.json()
        from pydantic import SecretStr

        from mockazure.db.engine import close_database, create_database
        from mockazure.db.migrations import migrate
        from tests.integration.conftest import isolated_database_url

        # An independent instance can import the original identity IDs. Importing
        # into a different tenant on this instance correctly rejects foreign subjects.
        with isolated_database_url() as fresh_url:
            migrate(fresh_url)
            fresh_settings = settings.model_copy(update={"database_url": SecretStr(fresh_url)})
            fresh_database = create_database(fresh_settings)
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=create_app(fresh_settings, fresh_database)),
                    base_url=str(client.base_url),
                    headers=dict(client.headers),
                ) as other:
                    destination = await other.post(
                        "/_mock/tenants",
                        json={
                            "slug": "ca-import",
                            "display_name": "Imported CA evidence",
                            "domain": "ca-import.test",
                        },
                    )
                    assert destination.status_code == 201
                    new_id = destination.json()["id"]
                    restored = await other.post(
                        f"/_mock/tenants/{new_id}/evidence/import", json=exported.json()
                    )
                    assert restored.status_code == 200, restored.text
                    await drain_api(other, new_id)
                    replay = await other.get(
                        f"/_mock/tenants/{new_id}/evidence/export?family=signIn"
                    )
                    assert replay.status_code == 200
                    assert replay.json()["records"] == exported.json()["records"]
            finally:
                await close_database(fresh_database)
        from mockazure.security.catalog import permission_id

        assert permission_id("Policy.Read.ConditionalAccess", "application") == UUID(
            "37730810-e9ba-4e46-b07e-8ca78d182097"
        )
