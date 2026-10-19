"""Queue snapshots preserve payloads explicitly but never carry receipt authority."""

from datetime import datetime

from pydantic import Field, model_validator
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.identity.types import IdentityInput
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.queue_models import QueueCursor, QueueMessage
from mockazure.storage.queues import checked_text


class QueueMessageInput(IdentityInput):
    queue: str
    body: str
    inserted_at: datetime
    expires_at: datetime | None = None
    visible_at: datetime
    dequeue_count: int = Field(ge=0, le=9223372036854775807)

    @model_validator(mode="after")
    def checked(self):
        try:
            checked_text(self.body)
        except StorageError:
            raise ValueError("Invalid Queue message text") from None
        if any(
            v.tzinfo is None
            for v in (self.inserted_at, self.visible_at, self.expires_at)
            if v is not None
        ):
            raise ValueError("Queue timestamps require timezones")
        if (
            self.visible_at < self.inserted_at
            or self.expires_at
            and self.expires_at <= self.inserted_at
        ):
            raise ValueError("Invalid Queue timeline")
        return self


async def export_queues(session, tenant, account):
    rows = list(
        await session.scalars(
            select(QueueMessage)
            .where(QueueMessage.tenant_id == tenant, QueueMessage.account_id == account)
            .order_by(QueueMessage.queue, QueueMessage.inserted_at, QueueMessage.id)
            .limit(100001)
        )
    )
    if len(rows) > 100000:
        raise AdminError(413, "CloudStateLimit", "Too many messages in Queue snapshot")
    return [
        QueueMessageInput(
            queue=r.queue,
            body=r.body,
            inserted_at=r.inserted_at,
            expires_at=r.expires_at,
            visible_at=r.visible_at,
            dequeue_count=r.dequeue_count,
        )
        for r in rows
    ]


async def restore_queues(session, tenant, account, values):
    for value in values:
        session.add(
            QueueMessage(
                tenant_id=tenant, account_id=account, receipt_hash=None, **value.model_dump()
            )
        )
    await session.flush()


async def prune_queues(session, tenant, now, limit):
    removed = 0
    for model in (QueueMessage, QueueCursor):
        if removed >= limit:
            break
        ids = list(
            await session.scalars(
                select(model.id)
                .where(model.tenant_id == tenant, model.expires_at <= now)
                .order_by(model.expires_at, model.id)
                .limit(limit - removed)
            )
        )
        if ids:
            await session.execute(delete(model).where(model.tenant_id == tenant, model.id.in_(ids)))
        removed += len(ids)
    return removed
