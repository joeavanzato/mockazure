"""Explicit v1.0 method writes against canonical enrollment descriptors."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import select

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.directory.models import DirectoryRelation, Group
from mockazure.identity.models import DirectoryRole
from mockazure.oauth.models import LoginSession
from mockazure.providers.graph.body import typed_body
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.security.audit import record_security_audit
from mockazure.security.methods import METHODS, enroll_method, invalid, method_user, remove_method
from mockazure.security.models import SecurityObject
from mockazure.security.permissions import forbidden
from mockazure.security.repository import lock_security, missing

WRITE_ROLES = frozenset(
    {
        "Authentication Administrator",
        "Privileged Authentication Administrator",
        "Global Administrator",
    }
)
ACTIONS = {
    "phoneMethods": frozenset({"POST", "PATCH", "DELETE"}),
    "emailMethods": frozenset({"POST", "PATCH", "DELETE"}),
    "microsoftAuthenticatorMethods": frozenset({"DELETE"}),
    "fido2Methods": frozenset({"DELETE"}),
}
# The official password-reset hierarchy also defines Authentication Administrator
# management of other users' non-password methods.
TARGET_ROLES = {
    "Authentication Administrator": {"Authentication Administrator"},
    "Directory Readers": {
        "Authentication Administrator",
        "User Administrator",
        "Helpdesk Administrator",
        "Password Administrator",
    },
    "Guest Inviter": {
        "Authentication Administrator",
        "User Administrator",
        "Helpdesk Administrator",
        "Password Administrator",
    },
    "Password Administrator": {
        "Authentication Administrator",
        "User Administrator",
        "Helpdesk Administrator",
        "Password Administrator",
    },
    "Groups Administrator": {"User Administrator"},
    "Helpdesk Administrator": {"User Administrator", "Helpdesk Administrator"},
    "User Administrator": {"User Administrator"},
    "Message Center Reader": {
        "Authentication Administrator",
        "User Administrator",
        "Helpdesk Administrator",
    },
    "Reports Reader": {
        "Authentication Administrator",
        "User Administrator",
        "Helpdesk Administrator",
    },
    "User Experience Success Manager": {
        "Authentication Administrator",
        "User Administrator",
        "Helpdesk Administrator",
    },
    "Usage Summary Reports Reader": {
        "Authentication Administrator",
        "User Administrator",
        "Helpdesk Administrator",
    },
}


async def roles_for(session, tenant, user):
    return set(
        await session.scalars(
            select(DirectoryRole.role).where(
                DirectoryRole.tenant_id == tenant, DirectoryRole.user_id == user
            )
        )
    )


async def require_target_authority(session, actor, target, *, reset=False):
    if actor.token_kind == "application":
        return
    roles = await roles_for(session, actor.tenant_id, actor.subject_id)
    if roles & {"Privileged Authentication Administrator", "Global Administrator"}:
        return
    supported = WRITE_ROLES | (
        {"User Administrator", "Helpdesk Administrator", "Password Administrator"}
        if reset
        else set()
    )
    candidates = roles & supported
    target_roles = await roles_for(session, actor.tenant_id, target.id)
    protected_group = await session.scalar(
        select(Group.id)
        .join(
            DirectoryRelation,
            (DirectoryRelation.group_id == Group.id)
            & (DirectoryRelation.tenant_id == Group.tenant_id),
        )
        .where(
            Group.tenant_id == actor.tenant_id,
            Group.deleted_at.is_(None),
            DirectoryRelation.object_id == target.id,
            Group.profile["isAssignableToRole"].as_boolean().is_(True),
        )
        .limit(1)
    )
    if protected_group or not candidates:
        forbidden()
    if target_roles and not any(
        all(role in TARGET_ROLES.get(target_role, set()) for target_role in target_roles)
        for role in candidates
    ):
        forbidden()


async def require_method_write(session, actor, user, collection, action):
    own = actor.token_kind == "delegated" and actor.subject_id == user.id
    if own and action != "DELETE":
        forbidden()
    family = METHODS[collection][1]
    permissions = frozenset(
        {"UserAuthenticationMethod.ReadWrite.All", f"UserAuthMethod-{family}.ReadWrite.All"}
    )
    delegated = permissions | (
        {"UserAuthenticationMethod.ReadWrite", f"UserAuthMethod-{family}.ReadWrite"}
        if own
        else set()
    )
    await permitted(
        actor,
        OperationRequirement(
            "graph", permissions, frozenset(delegated), frozenset() if own else WRITE_ROLES
        ),
        session,
    )
    if own:
        login = await session.get(LoginSession, actor.session_id) if actor.session_id else None
        now = datetime.now(UTC)
        if (
            login is None
            or login.tenant_id != actor.tenant_id
            or login.user_id != user.id
            or login.client_id != actor.client_id
            or login.revoked_at
            or login.expires_at <= now
            or login.authenticated_at is None
            or not now - timedelta(minutes=10) <= login.authenticated_at <= now
        ):
            raise GraphError(
                403, "insufficient_claims", "A recent local interactive sign-in is required"
            )
    else:
        await require_target_authority(session, actor, user)


async def mutate_method(
    session, context, user_id: UUID, kind: str, method_id: str | None, action: str, payload: dict
) -> dict | None:
    if action not in ACTIONS.get(kind, ()):
        raise GraphError(501, "NotSupported", "This method operation is not implemented")
    await lock_security(session, context.tenant_id)
    user = await method_user(session, context.tenant_id, user_id)
    await require_method_write(session, context, user, kind, action)
    graph_kind = METHODS[kind][0]
    body = typed_body(payload, graph_kind)
    before = None
    if action != "POST":
        row = await session.get(
            SecurityObject, (context.tenant_id, uuid5(user.id, "authenticationMethod:" + method_id))
        )
        if (
            row is None
            or row.kind != "authenticationMethod"
            or row.properties.get("userId") != str(user.id)
            or row.properties.get("payload", {}).get("@odata.type")
            != "#microsoft.graph." + graph_kind
        ):
            missing()
        before = dict(row.properties["payload"])
    if action == "DELETE":
        if body:
            invalid("DELETE has no request body")
        await remove_method(session, context.tenant_id, user, method_id)
        after = None
    else:
        if action == "PATCH":
            allowed = {"phoneNumber", "phoneType"} if kind == "phoneMethods" else {"emailAddress"}
            if not body or set(body) - allowed:
                invalid("Unsupported method update field")
            if "phoneType" in body and body["phoneType"] != before["phoneType"]:
                invalid("A phone method's phoneType cannot change")
            updated = {key: value for key, value in before.items() if key in allowed}
            updated.update(body)
            # Reuse the canonical validator and fixed-ID allocator atomically.
            await session.delete(row)
            await session.flush()
            body = updated
        after = await enroll_method(
            session,
            context.tenant_id,
            user,
            {"@odata.type": "#microsoft.graph." + graph_kind, **body},
        )
    await record_security_audit(
        session,
        context,
        {"POST": "Add", "PATCH": "Update", "DELETE": "Delete"}[action]
        + " user authentication method",
        {
            "id": (after or before)["id"],
            "displayName": user.display_name,
            "userPrincipalName": user.upn,
            "type": graph_kind,
        },
        before,
        after,
        datetime.now(UTC),
    )
    return after if action == "POST" else None
