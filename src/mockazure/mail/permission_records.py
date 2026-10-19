"""Imported ACL rows are observable evidence, never Exchange command grants."""

from uuid import UUID

from sqlalchemy import select

from mockazure.mail.authorization import require_mailbox_access
from mockazure.mail.collection_models import ExchangePermissionRecord
from mockazure.mail.repository import resolve_mailbox


async def read_mailbox_permissions(
    session, context, mailbox_user_id: UUID, recipient: bool, *, operation: str | None = None
) -> list[dict]:
    box = await resolve_mailbox(session, context.tenant_id, str(mailbox_user_id))
    command = "Get-RecipientPermission" if recipient else "Get-MailboxPermission"
    await require_mailbox_access(session, context, box, operation or command)
    rows = await session.scalars(
        select(ExchangePermissionRecord)
        .where(
            ExchangePermissionRecord.tenant_id == context.tenant_id,
            ExchangePermissionRecord.mailbox_user_id == mailbox_user_id,
            ExchangePermissionRecord.family == ("recipient" if recipient else "mailbox"),
        )
        .order_by(ExchangePermissionRecord.ordinal, ExchangePermissionRecord.id)
    )
    from mockazure.mail.delegation import delegation_rows

    return [{"Identity": box.primary_smtp, **row.payload} for row in rows] + await delegation_rows(
        session, box, recipient
    )
