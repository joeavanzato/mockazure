"""Mailbox lookup always includes tenant and the current canonical user."""

from uuid import UUID

from sqlalchemy import func, or_, select

from mockazure.identity.models import User
from mockazure.mail.models import Mailbox
from mockazure.providers.graph.errors import GraphError


async def resolve_mailbox(session, tenant: UUID, identity: str) -> Mailbox:
    if not isinstance(identity, str) or not identity or len(identity) > 320:
        raise GraphError(400, "ErrorInvalidMailbox", "Invalid mailbox identity")
    key = identity.lower()
    matches = [
        func.lower(User.upn) == key,
        func.lower(User.display_name) == key,
        Mailbox.primary_smtp == key,
        Mailbox.alias == key,
        Mailbox.aliases.contains([key]),
    ]
    try:
        identifier = UUID(identity)
        matches.extend([Mailbox.user_id == identifier, Mailbox.exchange_guid == identifier])
    except ValueError:
        pass
    rows = list(
        await session.scalars(
            select(Mailbox)
            .join(User, (User.tenant_id == Mailbox.tenant_id) & (User.id == Mailbox.user_id))
            .where(Mailbox.tenant_id == tenant, User.deleted_at.is_(None), or_(*matches))
            .limit(2)
        )
    )
    if not rows:
        raise GraphError(404, "ErrorMailboxNotFound", "Mailbox was not found")
    if len(rows) != 1:
        raise GraphError(400, "ErrorAmbiguousMailbox", "Mailbox identity is ambiguous")
    return rows[0]
