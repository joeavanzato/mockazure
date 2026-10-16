from dataclasses import dataclass

from sqlalchemy import select

from mockazure.identity.models import (
    Application,
    DirectoryRole,
    ServicePrincipal,
    ServicePrincipalRole,
    User,
)
from mockazure.identity.types import PrincipalContext


@dataclass(frozen=True)
class OperationRequirement:
    service: str
    application_permissions: frozenset[str]
    delegated_permissions: frozenset[str]
    user_roles: frozenset[str] = frozenset()
    access_rule: str = "tenant"
    application_roles: frozenset[str] = frozenset()


class AuthorizationDenied(Exception):
    pass


async def require_current_identity(context: PrincipalContext, session) -> None:
    app = await session.get(Application, context.client_id)
    principal = await session.scalar(
        select(ServicePrincipal).where(
            ServicePrincipal.tenant_id == context.tenant_id,
            ServicePrincipal.client_id == context.client_id,
            ServicePrincipal.enabled.is_(True),
        )
    )
    if app is None or not app.enabled or principal is None:
        raise AuthorizationDenied("Application identity is unavailable")
    if context.token_kind == "delegated":
        user = await session.scalar(
            select(User).where(
                User.tenant_id == context.tenant_id,
                User.id == context.subject_id,
                User.enabled.is_(True),
                User.deleted_at.is_(None),
            )
        )
        if user is None:
            raise AuthorizationDenied("Signed-in user cannot access this resource")


async def authorize(context: PrincipalContext, operation: OperationRequirement, session) -> None:
    await require_current_identity(context, session)
    if context.token_kind == "application":
        if not context.permissions & operation.application_permissions:
            raise AuthorizationDenied("Application permission is required")
        if operation.application_roles:
            roles = set(
                await session.scalars(
                    select(ServicePrincipalRole.role).where(
                        ServicePrincipalRole.tenant_id == context.tenant_id,
                        ServicePrincipalRole.client_id == context.client_id,
                    )
                )
            )
            if not roles & operation.application_roles:
                raise AuthorizationDenied("Service principal operational role is required")
        return
    if not context.permissions & operation.delegated_permissions:
        raise AuthorizationDenied("Delegated permission is required")
    user = await session.scalar(
        select(User).where(
            User.tenant_id == context.tenant_id,
            User.id == context.subject_id,
            User.enabled.is_(True),
            User.deleted_at.is_(None),
        )
    )
    if user is None:
        raise AuthorizationDenied("Signed-in user cannot access this resource")
    basic_organization = (
        operation.access_rule == "organization" and "User.Read" in context.permissions
    )
    if operation.user_roles and not basic_organization:
        roles = set(
            await session.scalars(
                select(DirectoryRole.role).where(
                    DirectoryRole.tenant_id == context.tenant_id, DirectoryRole.user_id == user.id
                )
            )
        )
        if not roles & operation.user_roles:
            raise AuthorizationDenied("Directory role is required")
