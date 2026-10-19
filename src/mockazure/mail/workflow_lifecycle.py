"""Transient mail authority never survives fixture replacement or reset."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update

from mockazure.db.job_model import Job
from mockazure.mail.send_models import MailAttachmentUpload, MailDelivery


async def retire_mail_workflows(session, tenant, user):
    ids = select(MailDelivery.id).where(
        MailDelivery.tenant_id == tenant, MailDelivery.user_id == user
    )
    await session.execute(
        update(Job)
        .where(Job.tenant_id == tenant, Job.id.in_(ids), Job.state.in_(["pending", "running"]))
        .values(
            state="failed",
            error_code="MailStateReplaced",
            claim_epoch=Job.claim_epoch + 1,
            lease_owner=None,
            lease_until=None,
            updated_at=datetime.now(UTC),
        )
    )
    await session.execute(
        delete(MailDelivery).where(MailDelivery.tenant_id == tenant, MailDelivery.user_id == user)
    )
    await session.execute(
        delete(MailAttachmentUpload).where(
            MailAttachmentUpload.tenant_id == tenant, MailAttachmentUpload.user_id == user
        )
    )


async def prune_mail_workflows(session, tenant, now, limit):
    from mockazure.mail.send import lock_workflow

    await lock_workflow(session, tenant)
    tokens = list(
        await session.scalars(
            select(MailAttachmentUpload.token_hash)
            .where(MailAttachmentUpload.tenant_id == tenant, MailAttachmentUpload.expires_at <= now)
            .order_by(MailAttachmentUpload.expires_at, MailAttachmentUpload.token_hash)
            .limit(limit)
        )
    )
    if tokens:
        await session.execute(
            delete(MailAttachmentUpload).where(MailAttachmentUpload.token_hash.in_(tokens))
        )
    remaining = limit - len(tokens)
    ids = (
        list(
            await session.scalars(
                select(MailDelivery.id)
                .join(Job, Job.id == MailDelivery.id)
                .where(
                    MailDelivery.tenant_id == tenant,
                    Job.state.in_(["failed", "succeeded"]),
                    Job.updated_at < now - timedelta(days=1),
                )
                .order_by(Job.updated_at, Job.id)
                .limit(remaining)
            )
        )
        if remaining
        else []
    )
    if ids:
        await session.execute(delete(MailDelivery).where(MailDelivery.id.in_(ids)))
    return len(tokens) + len(ids)
