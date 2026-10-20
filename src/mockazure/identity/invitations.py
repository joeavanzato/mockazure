"""Local invitation simulation; no email transport or external identity exchange."""

import hashlib
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select, update

from mockazure.admin.errors import AdminError
from mockazure.authorization.evaluator import OperationRequirement
from mockazure.db.models import Tenant
from mockazure.directory.history import record_change, record_object_version, reserve_revision
from mockazure.directory.mutations import AdminActor, DirectoryCommand
from mockazure.directory.repository import public_object
from mockazure.directory.types import DirectoryChange
from mockazure.evidence.directory_facts import capture_directory_facts
from mockazure.identity.invitation_models import GuestInvitation, GuestPolicy
from mockazure.identity.models import User
from mockazure.oauth.models import LoginSession
from mockazure.providers.graph.body import invalid
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.audit import record_security_audit
from mockazure.security.repository import lock_security


def email_address(value):
    if not isinstance(value, str) or not 3 <= len(value) <= 254:
        invalid("A bounded invitation email address is required")
    value = value.lower()
    if (
        re.fullmatch(r"[a-z0-9_](?:[a-z0-9_.-]*[a-z0-9_])?@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value)
        is None
    ):
        invalid("Email address is outside the documented invitation character profile")
    if ".." in value:
        invalid("Invalid invitation email address")
    return value


def validate_invitation(body, origins):
    if not isinstance(body, dict) or set(body) - {
        "invitedUserEmailAddress",
        "invitedUserDisplayName",
        "invitedUserType",
        "inviteRedirectUrl",
        "sendInvitationMessage",
        "invitedUserMessageInfo",
        "resetRedemption",
        "invitedUser",
    }:
        invalid("Unsupported invitation properties")
    try:
        validate_payload(
            "v1.0",
            "invitation",
            {k: v for k, v in body.items() if k != "invitedUser"},
            partial=True,
        )
    except ValueError:
        invalid("Invitation fields do not match the pinned Graph v1.0 schema")
    email = email_address(body.get("invitedUserEmailAddress"))
    if body.get("invitedUserType", "Guest") != "Guest":
        invalid("Only external Guest invitations are supported")
    for key in ("sendInvitationMessage", "resetRedemption"):
        if key in body and type(body[key]) is not bool:
            invalid(key + " must be Boolean")
    name = body.get("invitedUserDisplayName", email)
    if not isinstance(name, str) or not 1 <= len(name) <= 200 or "\x00" in name:
        invalid("Invitation display name must be 1..200 characters")
    redirect = body.get("inviteRedirectUrl")
    if not isinstance(redirect, str) or not 1 <= len(redirect) <= 2048:
        invalid("A configured local redirect URL is required")
    try:
        parsed = urlsplit(redirect)
        origin = parsed.scheme + "://" + parsed.netloc
        valid = (
            origin in origins
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and not parsed.path.startswith("//")
            and not any(ord(x) < 32 for x in redirect)
            and "\\" not in redirect
        )
    except ValueError:
        valid = False
    if not valid:
        invalid("Invitation redirect must use a configured local browser origin")
    message = body.get("invitedUserMessageInfo") or {}
    if not isinstance(message, dict) or set(message) - {
        "customizedMessageBody",
        "messageLanguage",
        "ccRecipients",
    }:
        invalid("Unsupported invitation message configuration")
    text = message.get("customizedMessageBody", "You are invited to this MockAzure lab.")
    if not isinstance(text, str) or len(text) > 4096 or "\x00" in text:
        invalid("Invitation text must be at most 4096 characters")
    language = message.get("messageLanguage", "en-US")
    if (
        not isinstance(language, str)
        or re.fullmatch(r"[a-z]{2}(?:-[A-Za-z]{2,8})?", language) is None
    ):
        invalid("Unsupported invitation language")
    cc = message.get("ccRecipients", [])
    if not isinstance(cc, list) or len(cc) > 1:
        invalid("At most one invitation CC recipient is supported")
    for value in cc:
        if not isinstance(value, dict) or set(value) != {"emailAddress"}:
            invalid("Invalid CC recipient")
        address = value["emailAddress"]
        if not isinstance(address, dict) or set(address) - {"address", "name"}:
            invalid("Invalid CC emailAddress")
        email_address(address.get("address"))
    return email, name, message, text


def view(row, user, origin):
    value = {
        "id": str(row.id),
        "invitedUserEmailAddress": row.email,
        "invitedUserDisplayName": user.display_name,
        "invitedUserType": "Guest",
        "inviteRedirectUrl": row.redirect_url,
        "sendInvitationMessage": row.send_message,
        "invitedUserMessageInfo": row.message_info,
        "status": row.status,
        "inviteRedeemUrl": f"{origin}/_mock/tenants/{row.tenant_id}/guests/{row.id}/redeem",
        "invitedUser": {
            "id": str(user.id),
            "userPrincipalName": user.upn,
            "displayName": user.display_name,
            "userType": "Guest",
        },
    }
    validate_payload(
        "v1.0", "invitation", {k: v for k, v in value.items() if k != "invitedUser"}, partial=True
    )
    validate_payload("v1.0", "user", value["invitedUser"], partial=True)
    return value


async def create_invitation(session, actor, body, wall_now, *, origins, origin):
    email, name, message, text = validate_invitation(body, origins)
    await lock_security(session, actor.tenant_id)
    policy = await session.get(GuestPolicy, actor.tenant_id)
    if policy and not policy.enabled:
        raise GraphError(403, "Authorization_RequestDenied", "Local B2B invitations are disabled")
    reset = body.get("resetRedemption", False)
    scopes = frozenset({"User.ReadWrite.All", "Directory.ReadWrite.All"})
    if not reset:
        scopes |= {"User.Invite.All"}
    roles = (
        frozenset({"Helpdesk Administrator", "User Administrator", "Global Administrator"})
        if reset
        else frozenset(
            {"Guest Inviter", "Directory Writers", "User Administrator", "Global Administrator"}
        )
        if policy and not policy.allow_member_invites
        else frozenset()
    )
    await permitted(actor, OperationRequirement("graph", scopes, scopes, roles), session)
    tenant = await session.get(Tenant, actor.tenant_id)
    if reset:
        target = body.get("invitedUser")
        try:
            if not isinstance(target, dict) or set(target) != {"id"}:
                raise ValueError()
            uid = UUID(target["id"])
        except (ValueError, TypeError, KeyError):
            invalid("resetRedemption requires invitedUser.id")
        user = await session.get(User, uid)
        if (
            not user
            or user.tenant_id != actor.tenant_id
            or user.deleted_at is not None
            or user.profile.get("userType") != "Guest"
        ):
            raise GraphError(404, "Request_ResourceNotFound", "Local guest was not found")
        if await session.scalar(
            select(User.id)
            .where(
                User.tenant_id == actor.tenant_id,
                User.id != user.id,
                User.deleted_at.is_(None),
                User.profile["mail"].as_string() == email,
                User.profile["userType"].as_string() == "Guest",
            )
            .limit(1)
        ):
            raise GraphError(
                409,
                "Request_MultipleObjectsWithSameKeyValue",
                "The invitation email belongs to another active guest",
            )
    else:
        if "invitedUser" in body:
            invalid("invitedUser is writable only for resetRedemption")
        user = await session.scalar(
            select(User)
            .where(
                User.tenant_id == actor.tenant_id,
                User.deleted_at.is_(None),
                User.profile["mail"].as_string() == email,
                User.profile["userType"].as_string() == "Guest",
            )
            .limit(1)
        )
    existing = user is not None
    before = public_object(user) if existing else None
    if not existing:
        prefix = email.replace("@", "_")
        available = 320 - len(tenant.domain) - len("#ext#@")
        if len(prefix) > available:
            prefix = (
                prefix[: available - 17] + "_" + hashlib.sha256(email.encode()).hexdigest()[:16]
            )
        upn = prefix + "#ext#@" + tenant.domain.lower()
        if await session.scalar(
            select(User.id).where(
                User.tenant_id == actor.tenant_id, User.upn == upn, User.deleted_at.is_(None)
            )
        ):
            raise GraphError(409, "Request_MultipleObjectsWithSameKeyValue", "Guest name conflicts")
        user = User(
            tenant_id=actor.tenant_id,
            upn=upn,
            display_name=name,
            enabled=True,
            verifier="!",
            profile={"userType": "Guest", "mail": email},
            created_at=wall_now,
        )
        session.add(user)
        await session.flush()
    profile = dict(user.profile)
    if reset or profile.get("externalUserState") not in {"Accepted", "PendingAcceptance"}:
        profile.update(
            externalUserState="PendingAcceptance",
            externalUserStateChangeDateTime=wall_now.isoformat(),
            _guestGeneration=str(uuid4()),
            mail=email,
        )
        if reset:
            user.credential_version += 1
            await session.execute(
                update(LoginSession)
                .where(
                    LoginSession.tenant_id == actor.tenant_id,
                    LoginSession.user_id == user.id,
                    LoginSession.revoked_at.is_(None),
                )
                .values(revoked_at=wall_now)
            )
    profile.setdefault("_guestGeneration", str(uuid4()))
    user.profile = profile
    after = public_object(user)
    fields = frozenset(
        k
        for k in after.properties
        if before is None or before.properties.get(k) != after.properties[k]
    )
    if fields:
        revision = await reserve_revision(session, actor.tenant_id)
        await record_object_version(
            session, DirectoryChange(revision, "user", user.id, fields), before, after
        )
    row = GuestInvitation(
        tenant_id=actor.tenant_id,
        user_id=user.id,
        generation=UUID(profile["_guestGeneration"]),
        email=email,
        status="Completed" if profile["externalUserState"] == "Accepted" else "PendingAcceptance",
        redirect_url=body["inviteRedirectUrl"],
        message_info=message,
        send_message=body.get("sendInvitationMessage", False),
        created_at=wall_now,
        delivery={
            "mode": "local-record",
            "to": email,
            "body": text,
            "ccRecipients": message.get("ccRecipients", []),
        }
        if body.get("sendInvitationMessage", False)
        else {"mode": "none"},
    )
    session.add(row)
    await session.flush()
    await record_security_audit(
        session,
        actor,
        "Invite external user",
        {"id": str(user.id), "type": "User", "displayName": user.display_name},
        {"externalUserState": before.properties.get("externalUserState")} if before else {},
        {"externalUserState": profile["externalUserState"]},
        wall_now,
    )
    return view(row, user, origin)


async def redeem_local_invitation(session, tenant_id, invitation_id, wall_now):
    await lock_security(session, tenant_id)
    row = await session.get(GuestInvitation, (tenant_id, invitation_id))
    user = await session.get(User, row.user_id) if row else None
    if (
        not row
        or not user
        or user.tenant_id != tenant_id
        or user.deleted_at is not None
        or user.profile.get("userType") != "Guest"
    ):
        raise AdminError(404, "InvitationNotFound", "Local invitation is unavailable")
    if str(row.generation) != user.profile.get("_guestGeneration"):
        raise AdminError(409, "InvitationSuperseded", "Use the current invitation after reset")
    if not user.enabled:
        raise AdminError(
            409, "GuestDisabled", "Enable the local guest explicitly before redemption"
        )
    if user.profile.get("externalUserState") != "Accepted":
        before = public_object(user)
        user.profile = {
            **user.profile,
            "externalUserState": "Accepted",
            "externalUserStateChangeDateTime": wall_now.isoformat(),
        }
        after = public_object(user)
        revision = await reserve_revision(session, tenant_id)
        facts = await capture_directory_facts(
            session,
            AdminActor("local-guest-redemption"),
            DirectoryCommand("user", "update", user.id, {}),
            before,
            after,
            uuid4(),
            [],
        )
        await record_change(
            session,
            DirectoryChange(
                revision,
                "user",
                user.id,
                frozenset(
                    {
                        "externalUserState",
                        "externalUserStateChangeDateTime",
                    }
                ),
            ),
            before,
            after,
            facts,
            occurred_at=wall_now,
        )
        await session.execute(
            update(GuestInvitation)
            .where(
                GuestInvitation.tenant_id == tenant_id,
                GuestInvitation.user_id == user.id,
                GuestInvitation.generation == row.generation,
            )
            .values(status="Completed")
        )
    return {
        "id": str(row.id),
        "userId": str(user.id),
        "status": "Completed",
        "externalUserState": "Accepted",
        "redirectUrl": row.redirect_url,
        "redemptionMode": "local-operator",
    }
