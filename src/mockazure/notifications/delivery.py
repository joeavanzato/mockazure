"""Current-authority, lease-fenced callbacks with bounded at-least-once retries."""

from datetime import timedelta
from time import monotonic

import httpx

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.jobs.repository import lock_claim
from mockazure.jobs.schemas import JobResult
from mockazure.mail.send import DeliveryLeaseExpired, current_stored_actor, lock_workflow
from mockazure.notifications.models import GraphNotification, GraphSubscription
from mockazure.notifications.receivers import post_receiver, resolve_receiver
from mockazure.notifications.subscriptions import require_current
from mockazure.providers.graph.errors import GraphError


async def process_notification(database, claim, wall_now):
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    try:
        async with database.sessions.begin() as session:
            await lock_workflow(session, claim.tenant_id)
            job = await lock_claim(session, claim, now())
            if job is None:
                return JobResult("stale", None, None)
            note = await session.get(GraphNotification, claim.id)
            sub = await session.get(GraphSubscription, note.subscription_id) if note else None
            invalidated = (
                not note
                or not sub
                or sub.tenant_id != claim.tenant_id
                or (
                    sub.deleted
                    and not (
                        note.payload.get("lifecycle") and note.change_type == "subscriptionRemoved"
                    )
                )
                or sub.revision != note.subscription_revision
                or sub.expires_at <= now()
            )
            if not invalidated:
                try:
                    actor = await current_stored_actor(session, sub.actor)
                    await require_current(session, actor, sub.target)
                    url = (
                        sub.payload.get("lifecycleNotificationUrl")
                        if note.payload.get("lifecycle")
                        else sub.payload["notificationUrl"]
                    )
                    binding = resolve_receiver(url, session.info.get("notification_receivers", {}))
                    if sub.receiver_bindings.get(url) != binding["fingerprint"]:
                        invalidated = True
                except (AuthorizationDenied, GraphError):
                    invalidated = True
            if not invalidated and note.deadline <= now():
                if (
                    not note.payload.get("lifecycle")
                    and sub.target["family"] in {"mail", "calendar"}
                    and sub.payload.get("lifecycleNotificationUrl")
                ):
                    from mockazure.notifications.publication import emit_lifecycle

                    await emit_lifecycle(session, sub, "missed", now())
                invalidated = True
            if invalidated:
                job.state = "failed"
                job.error_code = "NotificationInvalidated"
            else:
                remaining = (job.lease_until - now()).total_seconds()
                if remaining <= 1:
                    raise DeliveryLeaseExpired()
                value = {
                    "subscriptionId": str(sub.id),
                    "subscriptionExpirationDateTime": sub.expires_at.isoformat(),
                    "tenantId": str(sub.tenant_id),
                }
                if "clientState" in sub.payload:
                    value["clientState"] = sub.payload["clientState"]
                if note.payload.get("lifecycle"):
                    value.update(lifecycleEvent=note.change_type)
                else:
                    value.update(
                        changeType=note.change_type,
                        resource=note.payload["resource"],
                        resourceData=note.payload["resourceData"],
                    )
                from mockazure.notifications.contracts import validate_notification

                validate_notification(value)
                note.attempts += 1
                try:
                    status, _, _ = await post_receiver(
                        binding,
                        payload={"value": [value]},
                        timeout=min(3.0 if note.attempts == 1 else 10.0, remaining - 0.5),
                    )
                    succeeded = 200 <= status < 300
                except (httpx.HTTPError, OSError, ValueError):
                    succeeded = False
                if job.lease_until <= now():
                    raise DeliveryLeaseExpired()
                if succeeded:
                    job.state = "succeeded"
                    job.error_code = None
                    job.result = {"deliveredCount": 1, "deliveryAttempts": note.attempts}
                else:
                    job.state = "pending"
                    job.error_code = "NotificationReceiverUnavailable"
                    job.available_at = now() + timedelta(
                        seconds=min(900, 5 * 2 ** min(note.attempts - 1, 8))
                    )
                    # Generic jobs cap claim attempts at3. The notification's own
                    # counter and4h deadline govern its longer delivery contract.
                    job.attempts = min(job.attempts, 2)
            job.lease_owner = job.lease_until = None
            job.updated_at = now()
            await session.flush()
            return JobResult(job.state, job.result, job.error_code)
    except DeliveryLeaseExpired:
        return JobResult("stale", None, None)
