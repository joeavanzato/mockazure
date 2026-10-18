"""Bounded history cleanup coordinated with mailbox cursor issuance and mutations."""

from sqlalchemy import BigInteger, case, delete, exists, or_, select

from mockazure.content.models import ContentReference
from mockazure.mail.message_models import MailContentCursor, MailMessageVersion
from mockazure.mail.messages import owner_for
from mockazure.mail.models import Mailbox


def reclaimable(tenant_id, now):
    version, cursor = MailMessageVersion, MailContentCursor
    revision = case(
        (cursor.state["complete"].as_boolean().is_(True), cursor.state["upper"].astext),
        (cursor.state["lower"].astext.is_not(None), cursor.state["lower"].astext),
        else_=cursor.state["upper"].astext,
    ).cast(BigInteger)
    end = case((version.valid_to.is_not(None), version.valid_to), else_=version.valid_from)
    pinned = exists(
        select(1).where(
            cursor.tenant_id == version.tenant_id,
            cursor.user_id == version.user_id,
            cursor.expires_at > now,
            revision < end,
        )
    )
    return (
        version.tenant_id == tenant_id,
        or_(version.valid_to.is_not(None), version.deleted.is_(True)),
        ~pinned,
    )


async def cleanup_mail_content(session, tenant_id, now, limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be 1..1000")
    expired = list(
        await session.scalars(
            select(MailContentCursor.token_hash)
            .where(
                MailContentCursor.tenant_id == tenant_id,
                MailContentCursor.expires_at <= now,
            )
            .order_by(MailContentCursor.token_hash)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    if expired:
        await session.execute(
            delete(MailContentCursor).where(MailContentCursor.token_hash.in_(expired))
        )
    budget, versions = limit - len(expired), 0
    if budget:
        boxes = list(
            await session.scalars(
                select(Mailbox)
                .where(
                    Mailbox.tenant_id == tenant_id,
                    exists(
                        select(1).where(
                            *reclaimable(tenant_id, now),
                            MailMessageVersion.user_id == Mailbox.user_id,
                        )
                    ),
                )
                .order_by(Mailbox.user_id)
                .limit(budget)
                .with_for_update(skip_locked=True)
            )
        )
        for box in boxes:
            rows = list(
                await session.scalars(
                    select(MailMessageVersion)
                    .where(
                        *reclaimable(tenant_id, now),
                        MailMessageVersion.user_id == box.user_id,
                    )
                    .order_by(MailMessageVersion.immutable_id, MailMessageVersion.valid_from)
                    .limit(budget)
                )
            )
            for row in rows:
                await session.execute(
                    delete(ContentReference).where(
                        ContentReference.tenant_id == tenant_id,
                        ContentReference.owner_kind == "mail-version",
                        ContentReference.owner_id == owner_for(row, row.valid_from),
                    )
                )
                await session.delete(row)
            versions += len(rows)
            budget -= len(rows)
            if not budget:
                break
    await session.flush()
    remaining = await session.scalar(select(exists(select(1).where(*reclaimable(tenant_id, now)))))
    handles = await session.scalar(
        select(
            exists(
                select(1).where(
                    MailContentCursor.tenant_id == tenant_id,
                    MailContentCursor.expires_at <= now,
                )
            )
        )
    )
    return {
        "deleted": len(expired) + versions,
        "expired_handles": len(expired),
        "versions": versions,
        "remaining": bool(remaining or handles),
    }
