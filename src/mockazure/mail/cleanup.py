"""Reclaim closed rule history only while holding its mailbox snapshot lock."""

from sqlalchemy import BigInteger, String, delete, exists, literal, select, tuple_

from mockazure.directory.models import Continuation
from mockazure.mail.models import Mailbox, MailRuleVersion


def reclaimable(tenant_id, now):
    version = MailRuleVersion
    resource = (
        literal("users/")
        + version.user_id.cast(String)
        + literal("/mailFolders/inbox/messageRules")
    )
    revision = Continuation.state["upper_revision"].astext.cast(BigInteger)
    pinned = exists(
        select(1).where(
            Continuation.tenant_id == tenant_id,
            Continuation.expires_at > now,
            Continuation.state["binding"]["resource"].astext == resource,
            revision >= version.valid_from,
            revision < version.valid_to,
        )
    )
    return (
        version.tenant_id == tenant_id,
        version.valid_to.is_not(None),
        ~pinned,
    )


async def cleanup_rules(session, tenant_id, now, limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be 1..1000")
    expired = list(
        await session.scalars(
            select(Continuation.token_hash)
            .where(Continuation.tenant_id == tenant_id, Continuation.expires_at <= now)
            .order_by(Continuation.token_hash)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    if expired:
        await session.execute(delete(Continuation).where(Continuation.token_hash.in_(expired)))
    budget = limit - len(expired)
    candidates = exists(
        select(1).where(*reclaimable(tenant_id, now), MailRuleVersion.user_id == Mailbox.user_id)
    )
    mailboxes = (
        list(
            await session.scalars(
                select(Mailbox)
                .where(Mailbox.tenant_id == tenant_id, candidates)
                .order_by(Mailbox.user_id)
                .limit(budget)
                .with_for_update(skip_locked=True)
            )
        )
        if budget
        else []
    )
    versions = 0
    for mailbox in mailboxes:
        # Re-evaluate pins AFTER obtaining the lock also held during first-page issuance.
        keys = (
            await session.execute(
                select(MailRuleVersion.id, MailRuleVersion.valid_from)
                .where(*reclaimable(tenant_id, now), MailRuleVersion.user_id == mailbox.user_id)
                .order_by(MailRuleVersion.id, MailRuleVersion.valid_from)
                .limit(budget)
            )
        ).all()
        if keys:
            await session.execute(
                delete(MailRuleVersion).where(
                    MailRuleVersion.tenant_id == tenant_id,
                    MailRuleVersion.user_id == mailbox.user_id,
                    tuple_(MailRuleVersion.id, MailRuleVersion.valid_from).in_(keys),
                )
            )
            versions += len(keys)
            budget -= len(keys)
        if not budget:
            break
    remaining = await session.scalar(select(exists(select(1).where(*reclaimable(tenant_id, now)))))
    handles = await session.scalar(
        select(
            exists(
                select(1).where(Continuation.tenant_id == tenant_id, Continuation.expires_at <= now)
            )
        )
    )
    return {
        "deleted": len(expired) + versions,
        "expired_handles": len(expired),
        "versions": versions,
        "remaining": bool(remaining or handles),
    }
