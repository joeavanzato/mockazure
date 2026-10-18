"""Current identity, Exchange command and mailbox scope checks."""

from sqlalchemy import or_, select

from mockazure.authorization.evaluator import (
    AuthorizationDenied,
    OperationRequirement,
    authorize,
)
from mockazure.endpoints import RESOURCES
from mockazure.identity.models import DirectoryRole, ServicePrincipalRole, User
from mockazure.mail.contracts import operation_requirement
from mockazure.mail.models import ExchangeCommandGrant, MailSettings
from mockazure.mail.schemas import EXCHANGE_COMMANDS
from mockazure.mail.security_configuration_contracts import PARAMETERS as SECURITY_PARAMETERS

GRAPH = "00000003-0000-0000-c000-000000000000"
EXCHANGE = next(resource.audience for resource in RESOURCES if resource.service == "exchange")

READER_COMMANDS = frozenset(k for k in SECURITY_PARAMETERS if k.startswith("Get-")) | frozenset(
    {
        "Get-Recipient",
        "Get-EXORecipient",
        "Get-EXOMailboxPermission",
        "Get-EXORecipientPermission",
        "Get-Mailbox",
        "Get-EXOMailbox",
        "Get-EXOMailboxStatistics",
        "Get-EXOMailboxFolderStatistics",
        "Get-EXOCasMailbox",
        "Get-OrganizationConfig",
        "Get-TransportConfig",
        "Get-AcceptedDomain",
        "Get-TransportRule",
        "Get-MailboxPermission",
        "Get-RecipientPermission",
    }
)


def content_requirement(name, *, shared=False):
    if name == "settings":
        app_perms = delegated_perms = {"MailboxSettings.Read", "MailboxSettings.ReadWrite"}
    elif name == "write":
        app_perms = delegated_perms = {"Mail.ReadWrite"}
    elif name == "raw":
        app_perms = delegated_perms = {"Mail.Read", "Mail.ReadWrite"}
    elif name == "read":
        app_perms = {"Mail.ReadBasic.All", "Mail.Read", "Mail.ReadWrite"}
        delegated_perms = {"Mail.ReadBasic", "Mail.Read", "Mail.ReadWrite"}
    else:
        raise AuthorizationDenied("Unsupported content operation")
    shared_perms = {
        "read": {"Mail.ReadBasic.Shared", "Mail.Read.Shared", "Mail.ReadWrite.Shared"},
        "raw": {"Mail.Read.Shared", "Mail.ReadWrite.Shared"},
        "write": {"Mail.ReadWrite.Shared"},
    }.get(name, set())
    delegated_perms = shared_perms if shared else delegated_perms | shared_perms
    return OperationRequirement("graph", frozenset(app_perms), frozenset(delegated_perms))


async def require_mailbox_access(session, actor, mailbox, operation: str) -> None:
    if actor.tenant_id != mailbox.tenant_id:
        raise AuthorizationDenied("Mailbox belongs to another tenant")
    user = await session.get(User, mailbox.user_id)
    if user is None or user.tenant_id != actor.tenant_id or user.deleted_at is not None:
        raise AuthorizationDenied("Mailbox identity is unavailable")
    settings = await session.get(MailSettings, actor.tenant_id)
    if settings is None or not settings.exchange_enabled:
        raise AuthorizationDenied("Exchange service entitlement is required")
    if operation.startswith("content."):
        if actor.audience != GRAPH:
            raise AuthorizationDenied("Graph audience is required")
        shared = actor.token_kind == "delegated" and actor.subject_id != mailbox.user_id
        await authorize(
            actor, content_requirement(operation.removeprefix("content."), shared=shared), session
        )
        if shared:
            from mockazure.mail.delegation import has_delegation

            if not await has_delegation(
                session, actor.tenant_id, mailbox.user_id, actor.subject_id, "FullAccess"
            ):
                raise AuthorizationDenied("Current FullAccess mailbox delegation is required")
        return
    if operation.startswith("rules."):
        if actor.audience != GRAPH:
            raise AuthorizationDenied("Graph audience is required")
        await authorize(actor, operation_requirement(operation), session)
        if actor.token_kind == "delegated" and actor.subject_id != mailbox.user_id:
            raise AuthorizationDenied("Delegated mailbox ownership is required")
        return
    await require_exchange_command(session, actor, operation, mailbox.user_id)


async def require_exchange_command(
    session, actor, operation, mailbox_user_id=None, *, tenant_wide=False
):
    """None checks command availability for bootstrap; data reads still scope every mailbox."""
    if operation != "Get-OrganizationConfig":
        settings = await session.get(MailSettings, actor.tenant_id)
        if settings is None or not settings.exchange_enabled:
            raise AuthorizationDenied("Exchange service entitlement is required")
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
    scope = (
        []
        if mailbox_user_id is None
        else [
            or_(
                ExchangeCommandGrant.mailbox_user_id.is_(None),
                ExchangeCommandGrant.mailbox_user_id == mailbox_user_id,
            )
        ]
    )
    if tenant_wide:
        scope.append(ExchangeCommandGrant.mailbox_user_id.is_(None))
    permitted = await session.scalar(
        select(ExchangeCommandGrant.id)
        .where(
            ExchangeCommandGrant.tenant_id == actor.tenant_id,
            principal,
            ExchangeCommandGrant.commands.contains([operation]),
            *scope,
        )
        .limit(1)
    )
    if permitted is None:
        raise AuthorizationDenied("Exchange command and recipient scope are required")


def basic_content_only(actor, mailbox):
    shared = actor.token_kind == "delegated" and actor.subject_id != mailbox.user_id
    permissions = (
        {"Mail.Read.Shared", "Mail.ReadWrite.Shared"}
        if shared
        else {"Mail.Read", "Mail.ReadWrite", "Mail.Read.Shared", "Mail.ReadWrite.Shared"}
    )
    return not bool(actor.permissions & permissions)
