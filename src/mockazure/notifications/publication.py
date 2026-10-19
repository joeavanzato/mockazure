"""Notification outbox entries commit with source state; no network in source writes."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select

from mockazure.db.job_model import Job
from mockazure.notifications.models import GraphNotification, GraphSubscription


async def queue_notification(session, sub, source_key, change, payload, now, *, lifecycle=False):
    if sub.deleted or sub.expires_at <= now:
        return
    if not lifecycle and change not in sub.payload["changeType"].split(","):
        return
    # Preserve existing short keys; bound opaque source identities independently
    # of their public resource URI, which remains intact in the payload.
    if len(source_key) > 256:
        source_key = "sha256:" + sha256(source_key.encode("utf-8")).hexdigest()
    prior = await session.scalar(
        select(GraphNotification.id).where(
            GraphNotification.subscription_id == sub.id,
            GraphNotification.source_key == source_key,
            GraphNotification.change_type == change,
        )
    )
    if prior:
        return
    identifier = uuid4()
    job = Job(
        id=identifier,
        tenant_id=sub.tenant_id,
        kind="graph.notification",
        payload={"notification_id": str(identifier)},
        available_at=now,
    )
    session.add(job)
    await session.flush()
    session.add(
        GraphNotification(
            id=identifier,
            tenant_id=sub.tenant_id,
            subscription_id=sub.id,
            subscription_revision=sub.revision,
            source_key=source_key,
            change_type=change,
            payload={"lifecycle": lifecycle, **payload},
            attempts=0,
            deadline=now + timedelta(hours=4),
        )
    )
    await session.flush()


async def active_for(session, tenant, family, owner, now):
    return list(
        await session.scalars(
            select(GraphSubscription).where(
                GraphSubscription.tenant_id == tenant,
                GraphSubscription.deleted.is_(False),
                GraphSubscription.expires_at > now,
                GraphSubscription.target["family"].as_string() == family,
                GraphSubscription.target["owner"].as_string() == str(owner),
            )
        )
    )


async def publish_mail(session, row, previous, deleted, revision):
    now = datetime.now(UTC)
    for sub in await active_for(session, row.tenant_id, "mail", row.user_id, now):
        folder = sub.target.get("folder")
        old = (
            previous is not None
            and not previous.deleted
            and (not folder or previous.folder_id == folder)
        )
        new = not deleted and (not folder or row.folder_id == folder)
        if not old and not new:
            continue
        change = "updated" if old and new else "created" if new else "deleted"
        identifier = row.id if new else previous.id
        resource = f"users/{row.user_id}/messages/{identifier}"
        await queue_notification(
            session,
            sub,
            f"mail:{row.user_id}:{row.immutable_id}:{revision}",
            change,
            {
                "resource": resource,
                "resourceData": {
                    "@odata.type": "#microsoft.graph.message",
                    "@odata.id": resource,
                    "id": identifier,
                },
            },
            now,
        )


async def publish_calendar(session, box, row, existed, now):
    change = "deleted" if row.deleted else "updated" if existed else "created"
    for sub in await active_for(session, box.tenant_id, "calendar", box.user_id, now):
        resource = f"users/{box.user_id}/events/{row.id}"
        await queue_notification(
            session,
            sub,
            f"calendar:{box.user_id}:{row.id}:{row.revision}",
            change,
            {
                "resource": resource,
                "resourceData": {
                    "@odata.type": "#microsoft.graph.event",
                    "@odata.id": resource,
                    "id": row.id,
                },
            },
            now,
        )


async def publish_drive(session, tenant, scope, revision, changed, edges):
    from mockazure.workloads.drives import drive_tree

    now = datetime.now(UTC)
    subscriptions = await session.scalars(
        select(GraphSubscription).where(
            GraphSubscription.tenant_id == tenant,
            GraphSubscription.deleted.is_(False),
            GraphSubscription.expires_at > now,
            GraphSubscription.target["family"].as_string() == "drive",
            GraphSubscription.target["scope"].as_string() == scope,
        )
    )
    touched = {identifier for kind, identifier in changed if kind == "driveItem"}
    for source, relation, target in edges:
        if source[0] == "driveItem":
            touched.add(source[1])
        if target[0] == "driveItem":
            touched.add(target[1])
    for sub in subscriptions:
        drive = sub.target["drive"]
        affected = ("drive", drive) in changed or any(
            source == ("drive", drive) for source, _, _ in edges
        )
        if not affected and touched:
            for at in (revision - 1, revision):
                tree = drive_tree(tenant, scope, drive, at)
                if await session.scalar(select(tree.c.id).where(tree.c.id.in_(touched)).limit(1)):
                    affected = True
                    break
        if affected:
            resource = f"drives/{drive}/root"
            await queue_notification(
                session,
                sub,
                f"drive:{drive}:{revision}",
                "updated",
                {
                    "resource": resource,
                    "resourceData": {
                        "@odata.type": "#microsoft.graph.driveItem",
                        "@odata.id": resource,
                        "id": sub.target["root_public"],
                    },
                },
                now,
            )


async def emit_lifecycle(session, sub, event, now):
    from mockazure.providers.graph.errors import GraphError

    if sub.deleted or sub.expires_at <= now:
        raise GraphError(404, "ResourceNotFound", "Subscription is inactive")
    if event not in {"reauthorizationRequired", "missed", "subscriptionRemoved"}:
        raise GraphError(400, "InvalidRequest", "Unknown lifecycle event")
    if sub.target["family"] == "drive" and event != "reauthorizationRequired":
        raise GraphError(501, "NotSupported", "This lifecycle event is not supported for drives")
    if not sub.payload.get("lifecycleNotificationUrl"):
        raise GraphError(400, "InvalidRequest", "Subscription has no lifecycle receiver")
    if event == "subscriptionRemoved":
        sub.revision += 1
    await queue_notification(
        session, sub, "lifecycle:" + str(uuid4()), event, {}, now, lifecycle=True
    )
    if event == "subscriptionRemoved":
        sub.deleted = True
    sub.updated_at = now
    await session.flush()
