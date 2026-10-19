"""Export descriptions only; imported evidence never grants callback authority."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import Field, field_validator
from sqlalchemy import delete, select

from mockazure.db.job_model import Job
from mockazure.identity.types import IdentityInput
from mockazure.notifications.models import GraphNotification, GraphSubscription


class SubscriptionDescription(IdentityInput):
    application_id: UUID
    resource: str = Field(max_length=1024)
    change_type: str = Field(max_length=80)
    expiration: datetime

    @field_validator("expiration")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timezone required")
        return value


class NotificationStateInput(IdentityInput):
    subscriptions: list[SubscriptionDescription] = Field(default_factory=list, max_length=1000)


async def export_notifications(session, tenant):
    rows = await session.scalars(
        select(GraphSubscription)
        .where(GraphSubscription.tenant_id == tenant)
        .order_by(GraphSubscription.created_at, GraphSubscription.id)
    )
    return NotificationStateInput(
        subscriptions=[
            SubscriptionDescription(
                application_id=r.client_id,
                resource=r.payload["resource"],
                change_type=r.payload["changeType"],
                expiration=r.expires_at,
            )
            for r in rows
        ]
    )


async def replace_notifications(session, tenant, value):
    # Caller already holds lifecycle locks. Delete outbox jobs before subscription state.
    await session.execute(
        delete(Job).where(Job.tenant_id == tenant, Job.kind == "graph.notification")
    )
    await session.execute(delete(GraphSubscription).where(GraphSubscription.tenant_id == tenant))
    now = datetime.now(UTC)
    for item in value.subscriptions:
        session.add(
            GraphSubscription(
                id=uuid4(),
                tenant_id=tenant,
                client_id=item.application_id,
                subject_id=None,
                actor={},
                target={},
                receiver_bindings={},
                payload={"resource": item.resource, "changeType": item.change_type},
                revision=1,
                deleted=True,
                expires_at=item.expiration,
                created_at=now,
                updated_at=now,
            )
        )
    await session.flush()


async def prune_notifications(session, tenant, now, limit):
    ids = list(
        await session.scalars(
            select(GraphNotification.id)
            .join(Job, Job.id == GraphNotification.id)
            .where(
                GraphNotification.tenant_id == tenant,
                Job.state.in_(["failed", "succeeded"]),
                Job.updated_at < now - timedelta(days=1),
            )
            .order_by(Job.updated_at, Job.id)
            .limit(limit)
        )
    )
    if ids:
        await session.execute(delete(Job).where(Job.id.in_(ids)))
    used = len(ids)
    if used < limit:
        ids = list(
            await session.scalars(
                select(GraphSubscription.id)
                .where(
                    GraphSubscription.tenant_id == tenant,
                    GraphSubscription.expires_at < now - timedelta(days=1),
                    ~select(GraphNotification.id)
                    .where(GraphNotification.subscription_id == GraphSubscription.id)
                    .exists(),
                )
                .order_by(GraphSubscription.expires_at, GraphSubscription.id)
                .limit(limit - used)
            )
        )
        if ids:
            await session.execute(delete(GraphSubscription).where(GraphSubscription.id.in_(ids)))
        used += len(ids)
    return used
