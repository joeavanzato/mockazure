"""Canonical user lifecycle transitions; no external identity provider calls."""

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select, update

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.directory.history import record_object_version, reserve_revision
from mockazure.directory.repository import public_object
from mockazure.directory.types import DirectoryChange
from mockazure.identity.models import User
from mockazure.identity.types import PrincipalContext
from mockazure.oauth.models import LoginSession
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import permitted
from mockazure.security.audit import record_security_audit
from mockazure.security.method_mutations import require_target_authority
from mockazure.security.repository import lock_security


async def require_live_user(session, tenant_id: UUID, user_id: UUID) -> User:
    user = await session.scalar(
        select(User)
        .execution_options(populate_existing=True)
        .where(
            User.tenant_id == tenant_id,
            User.id == user_id,
            User.deleted_at.is_(None),
        )
    )
    if user is None:
        raise GraphError(404, "Request_ResourceNotFound", "User not found")
    return user


async def revoke_user_sessions(session, actor: PrincipalContext, user_id: UUID, wall_now) -> dict:
    await lock_security(session, actor.tenant_id)
    own = actor.token_kind == "delegated" and actor.subject_id == user_id
    await permitted(
        actor,
        OperationRequirement(
            "graph",
            frozenset({"User.RevokeSessions.All"}),
            frozenset({"User.RevokeSessions.All", "User.ReadWrite.All", "Directory.ReadWrite.All"}),
        ),
        session,
    )
    user = await require_live_user(session, actor.tenant_id, user_id)
    if not own:
        await require_target_authority(session, actor, user, reset=True)
    # Graph cannot invalidate an external guest's home-tenant authority.
    if user.profile.get("userType") == "Guest":
        return {"value": True}
    before = public_object(user)
    when = (
        max(wall_now, user.sessions_valid_from + timedelta(microseconds=1))
        if (user.sessions_valid_from is not None)
        else wall_now
    )
    user.sessions_valid_from = when
    await session.execute(
        update(LoginSession)
        .where(
            LoginSession.tenant_id == actor.tenant_id,
            LoginSession.user_id == user.id,
            LoginSession.revoked_at.is_(None),
        )
        .values(revoked_at=when)
    )
    revision = await reserve_revision(session, actor.tenant_id)
    after = public_object(user)
    await record_object_version(
        session,
        DirectoryChange(revision, "user", user.id, frozenset({"signInSessionsValidFromDateTime"})),
        before,
        after,
    )
    field = "signInSessionsValidFromDateTime"
    await record_security_audit(
        session,
        actor,
        "Revoke all refresh tokens",
        {"id": str(user.id), "type": "User", "displayName": user.display_name},
        {field: before.properties[field]},
        {field: after.properties[field]},
        wall_now,
    )
    return {"value": True}


async def set_manager(session, actor, user_id, manager_id, wall_now):
    await lock_security(session, actor.tenant_id)
    permissions = frozenset({"User.ReadWrite.All", "Directory.ReadWrite.All"})
    await permitted(
        actor,
        OperationRequirement(
            "graph",
            permissions,
            permissions,
            frozenset({"User Administrator", "Global Administrator"}),
        ),
        session,
    )
    user = await require_live_user(session, actor.tenant_id, user_id)
    if manager_id is not None:
        manager = await require_live_user(session, actor.tenant_id, manager_id)
        visited = {user.id}
        for _ in range(1000):
            if manager.id in visited:
                raise GraphError(
                    400, "Request_BadRequest", "Manager hierarchy cannot contain a cycle"
                )
            visited.add(manager.id)
            if manager.manager_id is None:
                break
            manager = await require_live_user(session, actor.tenant_id, manager.manager_id)
        else:
            raise GraphError(
                400, "Request_BadRequest", "Manager hierarchy exceeds local depth limit"
            )
    if user.manager_id == manager_id:
        return
    previous = str(user.manager_id) if user.manager_id else None
    before = public_object(user)
    user.manager_id = manager_id
    revision = await reserve_revision(session, actor.tenant_id)
    await record_object_version(
        session,
        DirectoryChange(revision, "user", user.id, frozenset()),
        before,
        public_object(user),
    )
    await record_security_audit(
        session,
        actor,
        "Update user",
        {"id": str(user.id), "type": "User", "displayName": user.display_name},
        {"manager": previous},
        {"manager": str(manager_id) if manager_id else None},
        wall_now,
    )
