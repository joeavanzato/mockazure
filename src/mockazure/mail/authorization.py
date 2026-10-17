"""Current identity, Exchange command and mailbox scope checks."""

from sqlalchemy import or_, select

from mockazure.authorization.evaluator import (
    AuthorizationDenied,
    OperationRequirement,
    authorize,
    require_current_identity,
)
from mockazure.identity.models import DirectoryRole, ServicePrincipalRole, User
from mockazure.mail.contracts import operation_requirement
from mockazure.mail.models import ExchangeCommandGrant, MailSettings
from mockazure.mail.schemas import EXCHANGE_COMMANDS

GRAPH = "00000003-0000-0000-c000-000000000000"
EXCHANGE = "00000002-0000-0ff1-ce00-000000000000"
READER_COMMANDS = frozenset(
    {
        "Get-Mailbox",
        "Get-EXOMailbox",
        "Get-EXOMailboxStatistics",
        "Get-EXOMailboxFolderStatistics",
        "Get-EXOCasMailbox",
        "Get-OrganizationConfig",
    }
)


async def require_mailbox_access(session, actor, mailbox, operation: str) -> None:
    if actor.tenant_id != mailbox.tenant_id:
        raise AuthorizationDenied("Mailbox belongs to another tenant")
    user = await session.get(User, mailbox.user_id)
    if user is None or user.tenant_id != actor.tenant_id or user.deleted_at is not None:
        raise AuthorizationDenied("Mailbox identity is unavailable")
    settings = await session.get(MailSettings, actor.tenant_id)
    if settings is None or not settings.exchange_enabled:
        raise AuthorizationDenied("Exchange service entitlement is required")
    if operation.startswith("rules."):
        if actor.audience != GRAPH:
            raise AuthorizationDenied("Graph audience is required")
        await authorize(actor, operation_requirement(operation), session)
        if actor.token_kind == "delegated" and actor.subject_id != mailbox.user_id:
            raise AuthorizationDenied("Delegated mailbox ownership is required")
        return
    if operation not in EXCHANGE_COMMANDS or actor.audience != EXCHANGE:
        raise AuthorizationDenied("Unsupported Exchange operation or audience")
    await authorize(
        actor,
        OperationRequirement(
            "exchange",
            frozenset({"Exchange.ManageAsApp"}),
            frozenset({"Exchange.Manage"}),
        ),
        session,
    )
    await require_current_identity(actor, session)
    if actor.token_kind == "application":
        roles = set(
            await session.scalars(
                select(ServicePrincipalRole.role).where(
                    ServicePrincipalRole.tenant_id == actor.tenant_id,
                    ServicePrincipalRole.client_id == actor.client_id,
                )
            )
        )
        principal = ExchangeCommandGrant.client_id == actor.client_id
    else:
        roles = set(
            await session.scalars(
                select(DirectoryRole.role).where(
                    DirectoryRole.tenant_id == actor.tenant_id,
                    DirectoryRole.user_id == actor.subject_id,
                )
            )
        )
        principal = ExchangeCommandGrant.user_id == actor.subject_id
    if roles & {"Global Administrator", "Exchange Administrator"}:
        return
    if "Global Reader" in roles and operation in READER_COMMANDS:
        return
    permitted = await session.scalar(
        select(ExchangeCommandGrant.id)
        .where(
            ExchangeCommandGrant.tenant_id == actor.tenant_id,
            principal,
            ExchangeCommandGrant.commands.contains([operation]),
            or_(
                ExchangeCommandGrant.mailbox_user_id.is_(None),
                ExchangeCommandGrant.mailbox_user_id == mailbox.user_id,
            ),
        )
        .limit(1)
    )
    if permitted is None:
        raise AuthorizationDenied("Exchange command and recipient scope are required")
