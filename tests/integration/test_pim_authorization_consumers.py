from datetime import UTC, datetime, timedelta

import pytest

from mockazure.authorization.roles import effective_roles
from mockazure.security.roles import role_ids
from tests.integration.graph_support import headers_for
from tests.integration.test_pim_lifecycle import ROOT, mfa_headers, pim_client, request_body


async def test_active_role_changes_evidence_access_without_new_token(
    database, settings, monkeypatch
):
    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        mfa = await mfa_headers(database, settings, tenant, app, user)
        path = "/v1.0/auditLogs/directoryAudits"
        assert (await client.get(path, headers=mfa)).status_code == 403

        assert (
            await client.post(
                ROOT + "/roleEligibilityScheduleRequests", headers=admin, json=request_body(user)
            )
        ).status_code == 201
        assert (await client.get(path, headers=mfa)).status_code == 403
        response = await client.post(
            ROOT + "/roleAssignmentScheduleRequests",
            headers=mfa,
            json=request_body(user, "selfActivate", "PT1H"),
        )
        assert response.status_code == 201, response.text
        assert (await client.get(path, headers=mfa)).status_code == 200
        from mockazure.authorization import roles

        # A trusted security-clock fixture, with no worker and no token reissue.
        with monkeypatch.context() as clock:
            clock.setattr(roles, "security_now", lambda: datetime.now(UTC) + timedelta(hours=2))
            assert (await client.get(path, headers=mfa)).status_code == 403
        async with database.sessions() as session:
            assert "Security Reader" in await effective_roles(
                session, tenant, user, datetime.now(UTC)
            )
        assert (
            await client.post(
                ROOT + "/roleAssignmentScheduleRequests",
                headers=mfa,
                json=request_body(user, "selfDeactivate"),
            )
        ).status_code == 201
        assert (await client.get(path, headers=mfa)).status_code == 403


@pytest.mark.parametrize(
    "role,permission,method,path,body,success",
    [
        ("Global Reader", "LicenseAssignment.Read.All", "GET", "/v1.0/subscribedSkus", None, 200),
        (
            "Authentication Administrator",
            "UserAuthenticationMethod.Read.All",
            "GET",
            "/v1.0/users/{target}/authentication/methods",
            None,
            200,
        ),
        (
            "User Administrator",
            "User.ReadWrite.All",
            "PATCH",
            "/v1.0/users/{target}",
            {"displayName": "PIM allowed edit"},
            204,
        ),
        (
            "Cloud Application Administrator",
            "Application.ReadWrite.All",
            "PATCH",
            "/v1.0/applications/{application}",
            {"displayName": "PIM allowed application edit"},
            204,
        ),
    ],
)
async def test_role_sensitive_consumers_observe_activation_and_expiry(
    database,
    settings,
    monkeypatch,
    role,
    permission,
    method,
    path,
    body,
    success,
):
    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        target = await client.post(
            f"/_mock/tenants/{tenant}/identity/users",
            json={
                "upn": "pim-target@contoso.test",
                "display_name": "PIM target",
                "password": "local-only-test-password",
            },
        )
        assert target.status_code == 201, target.text
        headers = await headers_for(
            database, settings, tenant, app, user, {permission}, "delegated"
        )
        app_headers = await headers_for(
            database, settings, tenant, app, None, {"Application.ReadWrite.All"}
        )
        application = await client.post(
            "/v1.0/applications", headers=app_headers, json={"displayName": "PIM managed target"}
        )
        assert application.status_code == 201, application.text
        path = path.format(target=target.json()["id"], application=application.json()["id"])

        async def operation():
            return await client.request(
                method, path, headers=headers, **({"json": body} if body else {})
            )

        denied = await operation()
        assert denied.status_code == 403, denied.text
        grant = request_body(user)
        grant["roleDefinitionId"] = role_ids()[role]
        granted = await client.post(
            ROOT + "/roleAssignmentScheduleRequests", headers=admin, json=grant
        )
        assert granted.status_code == 201, granted.text
        allowed = await operation()
        assert allowed.status_code == success, allowed.text
        from mockazure.authorization import roles

        with monkeypatch.context() as clock:
            clock.setattr(roles, "security_now", lambda: datetime.now(UTC) + timedelta(hours=3))
            expired = await operation()
            assert expired.status_code == 403, expired.text
        grant["action"] = "adminRemove"
        removed = await client.post(
            ROOT + "/roleAssignmentScheduleRequests", headers=admin, json=grant
        )
        assert removed.status_code == 201, removed.text
        assert (await operation()).status_code == 403


@pytest.mark.parametrize("temporary", [False, True])
async def test_disabled_privileged_target_remains_protected(database, settings, temporary):
    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        if temporary:
            body = request_body(user)
            body["roleDefinitionId"] = role_ids()["Global Administrator"]
            result = await client.post(
                ROOT + "/roleAssignmentScheduleRequests", headers=admin, json=body
            )
            assert result.status_code == 201, result.text
        else:
            result = await client.put(
                f"/_mock/tenants/{tenant}/identity/users/{user}/roles/Global Administrator"
            )
            assert result.status_code == 200, result.text
        disabled = await client.patch(
            f"/_mock/tenants/{tenant}/identity/users/{user}", json={"enabled": False}
        )
        assert disabled.status_code == 200, disabled.text
        async with database.sessions() as session:
            assert not await effective_roles(session, tenant, user, datetime.now(UTC))
        writer = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"User.EnableDisableAccount.All", "User.Read.All"},
        )
        changed = await client.patch(
            "/v1.0/users/" + str(user), headers=writer, json={"accountEnabled": True}
        )
        assert changed.status_code == 400, changed.text


async def test_service_principal_schedule_controls_exchange_by_object_id(
    database, settings, monkeypatch
):
    from sqlalchemy import select

    from mockazure.authorization import roles
    from mockazure.identity.models import ServicePrincipal
    from tests.integration.test_exchange_mail import command

    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        async with database.sessions() as session:
            principal_id = await session.scalar(
                select(ServicePrincipal.id).where(
                    ServicePrincipal.tenant_id == tenant,
                    ServicePrincipal.client_id == app,
                )
            )
        actor = await headers_for(
            database,
            settings,
            tenant,
            app,
            None,
            {"Exchange.ManageAsApp"},
            audience="https://outlook.office365.com",
        )
        before = await command(client, tenant, actor, "Get-OrganizationConfig", {})
        assert before.status_code == 403, before.text
        body = request_body(principal_id)
        body["roleDefinitionId"] = role_ids()["Exchange Administrator"]
        assigned = await client.post(
            ROOT + "/roleAssignmentScheduleRequests", headers=admin, json=body
        )
        assert assigned.status_code == 201, assigned.text
        allowed = await command(client, tenant, actor, "Get-OrganizationConfig", {})
        assert allowed.status_code == 200, allowed.text
        with monkeypatch.context() as clock:
            clock.setattr(roles, "security_now", lambda: datetime.now(UTC) + timedelta(hours=3))
            assert (
                await command(client, tenant, actor, "Get-OrganizationConfig", {})
            ).status_code == 403
