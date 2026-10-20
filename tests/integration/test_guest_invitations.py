from uuid import UUID, uuid4

from sqlalchemy import func, select

from mockazure.identity.models import Grant, User
from tests.integration.graph_support import graph_client, headers_for


async def invite(client, headers, settings, **extra):
    return await client.post(
        "/v1.0/invitations",
        headers=headers,
        json={
            "invitedUserEmailAddress": "guest@external.test",
            "inviteRedirectUrl": settings.endpoints.browser_login + "/_mock/ui/",
            **extra,
        },
    )


async def test_invitation_pending_then_local_acceptance(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Invite.All"})
        result = await invite(client, headers, settings)
        assert result.status_code == 201, result.text
        value = result.json()
        assert value["status"] == "PendingAcceptance"
        user = value["invitedUser"]["id"]
        read = await headers_for(database, settings, tenant, app, None, {"User.Read.All"})
        path = f"/v1.0/users/{user}?$select=id,userType,externalUserState"
        before = await client.get(path, headers=read)
        assert before.json()["externalUserState"] == "PendingAcceptance"
        assert before.json()["userType"] == "Guest"
        guest = await headers_for(database, settings, tenant, app, user, {"User.Read"}, "delegated")
        assert (await client.get("/v1.0/me", headers=guest)).status_code == 403
        async with database.sessions() as session:
            count = await session.scalar(select(func.count()).select_from(Grant))
        redeemed = await client.post(value["inviteRedeemUrl"])
        assert redeemed.status_code == 200, redeemed.text
        assert redeemed.json()["status"] == "Completed"
        after = await client.get(path, headers=read)
        assert after.json()["externalUserState"] == "Accepted"
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Grant)) == count
        assert (await client.get("/v1.0/me", headers=guest)).status_code == 200


async def test_duplicate_invitation_follows_declared_existing_user_contract(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Invite.All"})
        first = await invite(client, headers, settings)
        assert first.status_code == 201, first.text
        second = await invite(client, headers, settings)
        assert second.status_code == 201
        assert first.json()["invitedUser"]["id"] == second.json()["invitedUser"]["id"]
        assert (await client.post(first.json()["inviteRedeemUrl"])).status_code == 200
        again = await invite(client, headers, settings)
        assert again.json()["status"] == "Completed"
        assert again.json()["invitedUser"]["id"] == first.json()["invitedUser"]["id"]
        reset = await invite(
            client,
            headers,
            settings,
            resetRedemption=True,
            invitedUser={"id": first.json()["invitedUser"]["id"]},
        )
        assert reset.status_code == 403
        stronger = await headers_for(database, settings, tenant, app, None, {"User.ReadWrite.All"})
        reset = await invite(
            client,
            stronger,
            settings,
            resetRedemption=True,
            invitedUser={"id": first.json()["invitedUser"]["id"]},
        )
        assert reset.status_code == 201 and reset.json()["status"] == "PendingAcceptance"
        assert (await client.post(first.json()["inviteRedeemUrl"])).status_code == 409
        assert (await client.post(reset.json()["inviteRedeemUrl"])).status_code == 200


async def test_guest_membership_controls_resource_access(database, settings):
    from urllib.parse import quote

    from mockazure.population.catalog import get_adapter
    from mockazure.population.identity import resolve_identity
    from mockazure.population.transport import LocalTransport
    from mockazure.population.types import AdapterContext, Mutation
    from tests.integration.teams_support import seed_team
    from tests.integration.test_teams_mutations import team_member

    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Invite.All"})
        invited = await invite(client, headers, settings)
        assert invited.status_code == 201, invited.text
        user = invited.json()["invitedUser"]["id"]
        assert (await client.post(invited.json()["inviteRedeemUrl"])).status_code == 200
        ctx = AdapterContext(database, settings, tenant)
        await get_adapter("identity.clients").apply(
            ctx,
            [
                Mutation(
                    operation="provision",
                    logical_key="guest-reader",
                    payload={
                        "id": "guest-reader",
                        "display_name": "Guest reader",
                        "delegated_user_id": user,
                        "permissions": [
                            {
                                "resource": "graph",
                                "kind": "delegated",
                                "permissions": ["User.Read", "ChannelMessage.Read.All"],
                            }
                        ],
                    },
                )
            ],
        )
        transport = LocalTransport(ctx, await resolve_identity(ctx, "guest-reader"), client=client)
        assert (await transport.request("graph", "GET", "/v1.0/me")).status_code == 200
        fixture = await seed_team(client, tenant)
        path = fixture["base"] + "/messages"
        assert (await transport.request("graph", "GET", path)).status_code == 403
        write = await headers_for(
            database, settings, tenant, app, None, {"TeamMember.ReadWrite.All"}
        )
        membership = await team_member(client, tenant, write, fixture["team"], user)
        assert (await transport.request("graph", "GET", path)).status_code == 200
        removed = await client.delete(
            f"/v1.0/teams/{fixture['team']}/members/{quote(membership, safe='')}", headers=write
        )
        assert removed.status_code == 204
        assert (await transport.request("graph", "GET", path)).status_code == 403


async def test_redemption_is_tenant_bound_and_replay_safe(database, settings):
    from mockazure.evidence.models import EvidenceSource

    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Invite.All"})
        invited = await invite(client, headers, settings)
        assert invited.status_code == 201, invited.text
        url = invited.json()["inviteRedeemUrl"]
        assert (await client.post(url.replace(str(tenant), str(uuid4())))).status_code == 404
        assert (
            await client.post(url, headers={"Authorization": "Bearer invalid"})
        ).status_code == 401
        assert (await client.post(url)).status_code == 200
        async with database.sessions() as session:
            first = await session.scalar(select(func.count()).select_from(EvidenceSource))
        assert (await client.post(url)).status_code == 200
        async with database.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(EvidenceSource)) == first


async def test_invitation_never_sends_email_or_fetches_redirect(database, settings, monkeypatch):
    import smtplib
    import urllib.request

    from mockazure.population.catalog import get_adapter
    from mockazure.population.types import AdapterContext, ResourceQuery

    def outbound(*args, **kwargs):
        raise AssertionError("Invitation attempted external delivery")

    monkeypatch.setattr(smtplib, "SMTP", outbound)
    monkeypatch.setattr(urllib.request, "urlopen", outbound)
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Invite.All"})
        assert (
            await invite(client, headers, settings, inviteRedirectUrl="https://example.com/")
        ).status_code == 400
        result = await invite(
            client,
            headers,
            settings,
            sendInvitationMessage=True,
            invitedUserMessageInfo={
                "customizedMessageBody": "Local lab invitation",
                "ccRecipients": [{"emailAddress": {"address": "cc@external.test"}}],
            },
        )
        assert result.status_code == 201, result.text
        page = await get_adapter("identity.guestInvitations").browse(
            AdapterContext(database, settings, tenant),
            ResourceQuery(identifier=result.json()["id"]),
        )
        assert page.items[0].properties["deliveryMode"] == "local-record"
        assert page.items[0].properties["status"] == "PendingAcceptance"


async def test_external_session_limit_is_preserved(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(
            database, settings, tenant, app, None, {"User.Invite.All", "User.RevokeSessions.All"}
        )
        invited = await invite(client, headers, settings)
        assert invited.status_code == 201, invited.text
        assert (await client.post(invited.json()["inviteRedeemUrl"])).status_code == 200
        user = invited.json()["invitedUser"]["id"]
        revoked = await client.post(f"/v1.0/users/{user}/revokeSignInSessions", headers=headers)
        assert revoked.status_code == 200 and revoked.json()["value"] is True
        async with database.sessions() as session:
            assert (await session.get(User, UUID(user))).sessions_valid_from is None


async def test_invitation_policy_disables_app_only_and_bounds_member_invites(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        policy = f"/_mock/tenants/{tenant}/guests/policy"
        response = await client.put(policy, json={"enabled": False, "allow_member_invites": False})
        assert response.status_code == 200, response.text
        headers = await headers_for(database, settings, tenant, app, None, {"User.Invite.All"})
        assert (await invite(client, headers, settings)).status_code == 403
        assert (
            await client.put(policy, json={"enabled": True, "allow_member_invites": False})
        ).status_code == 200
        delegated = await headers_for(
            database, settings, tenant, app, user, {"User.Invite.All"}, "delegated"
        )
        assert (await invite(client, delegated, settings)).status_code == 403
        assert (await invite(client, headers, settings)).status_code == 201


async def test_invitation_and_acceptance_publish_valid_local_evidence(database, settings):
    from mockazure.evidence.models import EvidenceObservation
    from mockazure.providers.graph.schema_reference import validate_payload
    from tests.integration.test_evidence_directory import drain

    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Invite.All"})
        invited = await invite(client, headers, settings)
        assert invited.status_code == 201, invited.text
        assert (await client.post(invited.json()["inviteRedeemUrl"])).status_code == 200
        await drain(database)
        async with database.sessions() as session:
            rows = list(
                await session.scalars(
                    select(EvidenceObservation).where(
                        EvidenceObservation.tenant_id == tenant,
                        EvidenceObservation.family == "directoryAudit",
                    )
                )
            )
        payloads = [
            row.payload
            for row in rows
            if any(
                target["id"] == invited.json()["invitedUser"]["id"]
                for target in row.payload["targetResources"]
            )
        ]
        assert {"Invite external user", "Update user"} <= {
            x["activityDisplayName"] for x in payloads
        }
        for payload in payloads:
            validate_payload("v1.0", "directoryAudit", payload)


async def test_concurrent_reinvitations_reuse_one_guest_and_expose_navigation(database, settings):
    import asyncio

    from mockazure.population.navigation import related
    from mockazure.population.types import AdapterContext

    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Invite.All"})
        first, second = await asyncio.gather(
            invite(client, headers, settings), invite(client, headers, settings)
        )
        assert first.status_code == second.status_code == 201
        assert first.json()["invitedUser"]["id"] == second.json()["invitedUser"]["id"]
        navigation = await related(
            AdapterContext(database, settings, tenant),
            "identity.guestInvitations",
            first.json()["id"],
            None,
        )
        assert navigation["items"][0]["id"] == first.json()["invitedUser"]["id"]


async def test_reset_does_not_retarget_another_guests_email(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, {"User.ReadWrite.All"})
        first = await invite(client, headers, settings)
        second = await invite(
            client, headers, settings, invitedUserEmailAddress="second@external.test"
        )
        assert first.status_code == second.status_code == 201
        conflict = await invite(
            client,
            headers,
            settings,
            resetRedemption=True,
            invitedUser={"id": second.json()["invitedUser"]["id"]},
        )
        assert conflict.status_code == 409, conflict.text


async def test_guest_acceptance_state_survives_public_dataset_export(database, settings):
    from mockazure.directory.datasets import DirectoryDataset

    async with graph_client(database, settings) as (client, tenant, app, _):
        headers = await headers_for(database, settings, tenant, app, None, {"User.Invite.All"})
        invitation = await invite(client, headers, settings)
        assert invitation.status_code == 201
        exported = await client.get(f"/_mock/tenants/{tenant}/directory/export")
        assert exported.status_code == 200
        value = next(
            x for x in exported.json()["users"] if x["id"] == invitation.json()["invitedUser"]["id"]
        )
        assert value["externalUserState"] == "PendingAcceptance"
        assert value["externalUserStateChangeDateTime"]
        imported = DirectoryDataset.model_validate(exported.json())
        assert next(x for x in imported.users if str(x.id) == value["id"]).externalUserState == (
            "PendingAcceptance"
        )
