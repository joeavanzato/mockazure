"""Current role authority; eligibility and worker progress are never authorization."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, union

from mockazure.identity.models import (
    Application,
    DirectoryRole,
    ServicePrincipal,
    ServicePrincipalRole,
    User,
)
from mockazure.security.pim.models import PimSchedule


def security_now():
    return datetime.now(UTC)


def _role_rows(tenant_id: UUID, wall_now=None, *, include_disabled=False):
    now = wall_now or security_now()
    live_user = and_(
        User.tenant_id == tenant_id,
        True if include_disabled else User.enabled.is_(True),
        User.deleted_at.is_(None),
        True
        if include_disabled
        else (
            func.coalesce(User.profile["externalUserState"].as_string(), "") != "PendingAcceptance"
        ),
    )
    direct = (
        select(User.id.label("principal_id"), DirectoryRole.role.label("role"))
        .join(
            DirectoryRole,
            and_(DirectoryRole.tenant_id == User.tenant_id, DirectoryRole.user_id == User.id),
        )
        .where(live_user)
    )
    service = (
        select(ServicePrincipal.id.label("principal_id"), ServicePrincipalRole.role.label("role"))
        .join(
            ServicePrincipalRole,
            and_(
                ServicePrincipalRole.tenant_id == ServicePrincipal.tenant_id,
                ServicePrincipalRole.client_id == ServicePrincipal.client_id,
            ),
        )
        .join(
            Application,
            and_(
                Application.tenant_id == ServicePrincipal.tenant_id,
                Application.client_id == ServicePrincipal.client_id,
            ),
        )
        .where(
            ServicePrincipal.tenant_id == tenant_id,
            True if include_disabled else ServicePrincipal.enabled.is_(True),
            ServicePrincipal.deleted_at.is_(None),
            True if include_disabled else Application.enabled.is_(True),
            Application.deleted_at.is_(None),
        )
    )
    active = (
        select(User.id.label("principal_id"), PimSchedule.role.label("role"))
        .join(
            PimSchedule,
            and_(PimSchedule.tenant_id == User.tenant_id, PimSchedule.principal_id == User.id),
        )
        .where(
            live_user,
            PimSchedule.kind == "assignment",
            PimSchedule.revoked_at.is_(None),
            PimSchedule.starts_at <= now,
            or_(PimSchedule.ends_at.is_(None), PimSchedule.ends_at > now),
        )
    )
    active_sp = (
        select(ServicePrincipal.id.label("principal_id"), PimSchedule.role.label("role"))
        .join(
            PimSchedule,
            and_(
                PimSchedule.tenant_id == ServicePrincipal.tenant_id,
                PimSchedule.principal_id == ServicePrincipal.id,
            ),
        )
        .join(
            Application,
            and_(
                Application.tenant_id == ServicePrincipal.tenant_id,
                Application.client_id == ServicePrincipal.client_id,
            ),
        )
        .where(
            ServicePrincipal.tenant_id == tenant_id,
            True if include_disabled else ServicePrincipal.enabled.is_(True),
            ServicePrincipal.deleted_at.is_(None),
            True if include_disabled else Application.enabled.is_(True),
            Application.deleted_at.is_(None),
            PimSchedule.kind == "assignment",
            PimSchedule.revoked_at.is_(None),
            PimSchedule.starts_at <= now,
            or_(PimSchedule.ends_at.is_(None), PimSchedule.ends_at > now),
        )
    )
    return union(direct, service, active, active_sp).subquery()


def effective_role_rows(tenant_id: UUID, wall_now=None):
    return _role_rows(tenant_id, wall_now)


def protected_role_rows(tenant_id: UUID, wall_now=None):
    """Target risk classification, never caller authority; disabled admins stay protected."""
    return _role_rows(tenant_id, wall_now, include_disabled=True)


async def protected_roles(session, tenant_id: UUID, principal_id: UUID, wall_now=None):
    rows = protected_role_rows(tenant_id, wall_now)
    return frozenset(
        await session.scalars(select(rows.c.role).where(rows.c.principal_id == principal_id))
    )


async def effective_roles(session, tenant_id: UUID, principal_id: UUID, wall_now=None):
    rows = effective_role_rows(tenant_id, wall_now)
    return frozenset(
        await session.scalars(select(rows.c.role).where(rows.c.principal_id == principal_id))
    )


async def actor_roles(session, actor, wall_now=None):
    identifier = actor.subject_id
    if actor.token_kind == "application":
        identifier = await session.scalar(
            select(ServicePrincipal.id).where(
                ServicePrincipal.tenant_id == actor.tenant_id,
                ServicePrincipal.client_id == actor.client_id,
                ServicePrincipal.deleted_at.is_(None),
            )
        )
    return await effective_roles(session, actor.tenant_id, identifier, wall_now)
