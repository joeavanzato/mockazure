from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from mockazure.authorization.roles import effective_roles
from mockazure.identity.keys import SigningKeyStore, read_wrapping_key
from mockazure.identity.models import DirectoryRole
from mockazure.identity.tokens import issue_access_token
from mockazure.identity.types import PrincipalContext
from mockazure.oauth.models import LoginSession
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.roles import legacy_id, role_ids
from tests.integration.graph_support import graph_client, headers_for
from tests.integration.test_graph_licensing import seed_sku

ROOT = "/v1.0/roleManagement/directory"
ROLE = "Security Reader"


def request_body(user, action="adminAssign", duration="PT2H"):
    return {
        "principalId": str(user),
        "roleDefinitionId": role_ids()[ROLE],
        "directoryScopeId": "/",
        "action": action,
        "justification": "Investigate local lab",
        "scheduleInfo": {"expiration": {"type": "afterDuration", "duration": duration}},
    }


@asynccontextmanager
async def pim_client(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        await seed_sku(client, tenant)
        admin = await headers_for(
            database, settings, tenant, app, None, {"RoleManagement.ReadWrite.Directory"}
        )
        user_headers = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"RoleAssignmentSchedule.ReadWrite.Directory"},
            "delegated",
        )
        yield client, tenant, app, user, admin, user_headers


async def mfa_headers(database, settings, tenant, app, user):
    now = datetime.now(UTC)
    async with database.sessions.begin() as session:
        login = LoginSession(
            id=uuid4(),
            tenant_id=tenant,
            client_id=app,
            user_id=user,
            resource="graph",
            scopes=["RoleAssignmentSchedule.ReadWrite.Directory", "AuditLog.Read.All"],
            mfa="completed",
            created_at=now,
            authenticated_at=now,
            expires_at=now + timedelta(hours=1),
            credential_version=0,
        )
        session.add(login)
        await session.flush()
        context = PrincipalContext(
            tenant,
            app,
            user,
            "delegated",
            "00000003-0000-0000-c000-000000000000",
            frozenset(login.scopes),
            session_id=login.id,
        )
        store = SigningKeyStore(session, read_wrapping_key(settings.data_dir), settings.endpoints)
        token = await issue_access_token(context, now, store)
        return {"Authorization": "Bearer " + token}


async def test_eligibility_is_not_authority_and_activation_requires_self_mfa(database, settings):
    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        result = await client.post(
            ROOT + "/roleEligibilityScheduleRequests", headers=admin, json=request_body(user)
        )
        assert result.status_code == 201, result.text
        async with database.sessions() as session:
            assert ROLE not in await effective_roles(session, tenant, user, datetime.now(UTC))
        body = request_body(user, "selfActivate", "PT1H")
        denied = await client.post(
            ROOT + "/roleAssignmentScheduleRequests", headers=plain, json=body
        )
        assert denied.status_code == 403, denied.text
        mfa = await mfa_headers(database, settings, tenant, app, user)
        active = await client.post(ROOT + "/roleAssignmentScheduleRequests", headers=mfa, json=body)
        assert active.status_code == 201, active.text
        async with database.sessions() as session:
            assert ROLE in await effective_roles(session, tenant, user, datetime.now(UTC))
        deactivated = await client.post(
            ROOT + "/roleAssignmentScheduleRequests",
            headers=mfa,
            json=request_body(user, "selfDeactivate"),
        )
        assert deactivated.status_code == 201, deactivated.text
        async with database.sessions() as session:
            assert ROLE not in await effective_roles(session, tenant, user, datetime.now(UTC))


async def test_future_expired_and_cancelled_grants_never_authorize(database, settings):
    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        now = datetime.now(UTC)
        body = request_body(user)
        body["scheduleInfo"]["startDateTime"] = (now + timedelta(hours=1)).isoformat()
        response = await client.post(
            ROOT + "/roleAssignmentScheduleRequests", headers=admin, json=body
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "Granted"
        async with database.sessions() as session:
            assert ROLE not in await effective_roles(session, tenant, user, now)
            assert ROLE in await effective_roles(session, tenant, user, now + timedelta(hours=2))
            assert ROLE not in await effective_roles(
                session, tenant, user, now + timedelta(hours=4)
            )
        cancelled = await client.post(
            ROOT + "/roleAssignmentScheduleRequests/" + response.json()["id"] + "/cancel",
            headers=admin,
        )
        assert cancelled.status_code == 204, cancelled.text
        async with database.sessions() as session:
            assert ROLE not in await effective_roles(
                session, tenant, user, now + timedelta(hours=2)
            )
        async with database.sessions.begin() as session:
            session.add(DirectoryRole(tenant_id=tenant, user_id=user, role=ROLE))
        async with database.sessions() as session:
            assert ROLE in await effective_roles(session, tenant, user, now + timedelta(hours=4))


async def test_policy_bounds_approval_and_native_read_shapes(database, settings):
    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        policy_response = await client.get(
            "/v1.0/policies/roleManagementPolicyAssignments",
            headers=admin,
            params={"$filter": f"roleDefinitionId eq '{role_ids()[ROLE]}'"},
        )
        assert policy_response.status_code == 200, policy_response.text
        assignment = policy_response.json()["value"][0]
        validate_payload("v1.0", "unifiedRoleManagementPolicyAssignment", assignment)
        path = "/v1.0/policies/roleManagementPolicies/" + assignment["policyId"] + "/rules"
        rules_response = await client.get(path, headers=admin)
        assert rules_response.status_code == 200, rules_response.text
        rules = {row["id"]: row for row in rules_response.json()["value"]}
        for rule in rules.values():
            validate_payload(
                "v1.0",
                rule["@odata.type"].split(".")[-1],
                {k: v for k, v in rule.items() if not k.startswith("@")},
            )
        approval = rules["Approval_EndUser_Assignment"]
        approval["setting"]["isApprovalRequired"] = True
        patched = await client.patch(path + "/" + approval["id"], headers=admin, json=approval)
        assert patched.status_code == 204, patched.text
        result = await client.post(
            ROOT + "/roleEligibilityScheduleRequests", headers=admin, json=request_body(user)
        )
        assert result.status_code == 201, result.text
        mfa = await mfa_headers(database, settings, tenant, app, user)
        response = await client.post(
            ROOT + "/roleAssignmentScheduleRequests",
            headers=mfa,
            json=request_body(user, "selfActivate", "PT1H"),
        )
        assert response.status_code == 501, response.text
        async with database.sessions() as session:
            assert ROLE not in await effective_roles(session, tenant, user, datetime.now(UTC))
        approval["setting"]["isApprovalRequired"] = False
        assert (
            await client.patch(path + "/" + approval["id"], headers=admin, json=approval)
        ).status_code == 204
        expiration = rules["Expiration_EndUser_Assignment"]
        expiration["maximumDuration"] = "PT30M"
        assert (
            await client.patch(path + "/" + expiration["id"], headers=admin, json=expiration)
        ).status_code == 204
        response = await client.post(
            ROOT + "/roleAssignmentScheduleRequests",
            headers=mfa,
            json=request_body(user, "selfActivate", "PT1H"),
        )
        assert response.status_code == 400, response.text
        no_justification = request_body(user, "selfActivate", "PT15M")
        no_justification.pop("justification")
        assert (
            await client.post(
                ROOT + "/roleAssignmentScheduleRequests", headers=mfa, json=no_justification
            )
        ).status_code == 400
        response = await client.post(
            ROOT + "/roleAssignmentScheduleRequests",
            headers=mfa,
            json=request_body(user, "selfActivate", "PT15M"),
        )
        assert response.status_code == 201, response.text
        for label in ("Eligibility", "Assignment"):
            for suffix, typename in (("Requests", "Request"), ("s", ""), ("Instances", "Instance")):
                collection = "role" + label + "Schedule" + suffix
                read = await client.get(
                    ROOT + "/" + collection,
                    headers=admin,
                    params={"$filter": f"principalId eq '{user}'", "$top": "1"},
                )
                assert read.status_code == 200, read.text
                rows = read.json()["value"]
                assert len(rows) == 1, read.text
                validate_payload("v1.0", "unifiedRole" + label + "Schedule" + typename, rows[0])
                item = await client.get(
                    ROOT + "/" + collection + "/" + rows[0]["id"], headers=admin
                )
                assert item.status_code == 200, item.text
        # Legacy inventory shows activation, but cannot delete it as a direct role.
        roles = await client.get(ROOT + "/roleAssignments", headers=admin)
        assert roles.status_code == 200, roles.text
        active = [r for r in roles.json()["value"] if r["principalId"] == str(user)]
        assert len(active) == 1
        removed = await client.delete(ROOT + "/roleAssignments/" + active[0]["id"], headers=admin)
        assert removed.status_code == 400, removed.text


async def test_request_paging_is_caller_bound_and_current_user_is_scoped(database, settings):
    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        first = request_body(user)
        second = request_body(user)
        second["roleDefinitionId"] = role_ids()["Reports Reader"]
        for body in (first, second):
            result = await client.post(
                ROOT + "/roleEligibilityScheduleRequests", headers=admin, json=body
            )
            assert result.status_code == 201, result.text
        path = ROOT + "/roleEligibilityScheduleRequests"
        page = await client.get(path, headers=admin, params={"$top": "1"})
        assert page.status_code == 200, page.text
        next_link = page.json()["@odata.nextLink"]
        second_page = await client.get(next_link, headers=admin)
        assert second_page.status_code == 200, second_page.text
        assert second_page.json()["value"][0]["id"] != page.json()["value"][0]["id"]
        eligible_user = await headers_for(
            database,
            settings,
            tenant,
            app,
            user,
            {"RoleEligibilitySchedule.ReadWrite.Directory"},
            "delegated",
        )
        own = await client.get(path + "/filterByCurrentUser(on='principal')", headers=eligible_user)
        assert own.status_code == 200, own.text
        assert len(own.json()["value"]) == 2
        denied = await client.get(next_link, headers=eligible_user)
        assert denied.status_code in (400, 403), denied.text


async def test_direct_and_pim_grants_share_one_legacy_member(database, settings):
    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        granted = await client.post(
            ROOT + "/roleAssignmentScheduleRequests", headers=admin, json=request_body(user)
        )
        assert granted.status_code == 201, granted.text
        async with database.sessions.begin() as session:
            session.add(DirectoryRole(tenant_id=tenant, user_id=user, role=ROLE))
        members = await client.get(
            "/v1.0/directoryRoles/" + str(legacy_id(tenant, ROLE)) + "/members",
            headers=admin,
        )
        assert members.status_code == 200, members.text
        assert len(members.json()["value"]) == 1


async def test_canceled_request_retention_does_not_remove_audit(database, settings, monkeypatch):
    from sqlalchemy import func, select

    from mockazure.evidence.models import EvidenceSource
    from mockazure.providers.graph.security import pim_routes

    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        now = datetime.now(UTC)
        body = request_body(user)
        body["scheduleInfo"]["startDateTime"] = (now + timedelta(hours=1)).isoformat()
        result = await client.post(
            ROOT + "/roleAssignmentScheduleRequests", headers=admin, json=body
        )
        assert result.status_code == 201, result.text
        path = ROOT + "/roleAssignmentScheduleRequests/" + result.json()["id"]
        assert (await client.post(path + "/cancel", headers=admin)).status_code == 204
        assert (await client.get(path, headers=admin)).json()["status"] == "Canceled"
        count = (
            select(func.count())
            .select_from(EvidenceSource)
            .where(EvidenceSource.tenant_id == tenant, EvidenceSource.kind == "security")
        )
        async with database.sessions() as session:
            before = await session.scalar(count)
        monkeypatch.setattr(pim_routes, "security_now", lambda: now + timedelta(days=31))
        assert (await client.get(path, headers=admin)).status_code == 404
        async with database.sessions() as session:
            assert await session.scalar(count) == before


@pytest.mark.parametrize(
    "rule,body",
    [
        ("Expiration_Admin_Eligibility", {"isExpirationRequired": None}),
        ("Enablement_EndUser_Assignment", {"enabledRules": None}),
        ("Approval_EndUser_Assignment", {"setting": None}),
    ],
)
async def test_null_policy_controls_fail_explicitly(database, settings, rule, body):
    from mockazure.security.pim.policies import policy_id

    async with pim_client(database, settings) as (client, tenant, app, user, admin, plain):
        path = "/v1.0/policies/roleManagementPolicies/" + policy_id(tenant, ROLE) + "/rules/" + rule
        before = (await client.get(path, headers=admin)).json()
        response = await client.patch(path, headers=admin, json=body)
        assert response.status_code == 400, response.text
        assert (await client.get(path, headers=admin)).json() == before
