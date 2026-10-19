"""Generation-fenced meeting state and local invitation/response messages."""

import copy
from datetime import timedelta
from time import monotonic
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.calendar.events import (
    calendar_for,
    event_view,
    require_calendar_access,
    save_event,
    state_for,
)
from mockazure.calendar.models import CalendarEvent, CalendarState, MeetingDelivery
from mockazure.cloud.events import lock_cloud_state
from mockazure.db.job_model import Job
from mockazure.evidence.repository import lock_state
from mockazure.jobs.repository import fail_job, lock_claim
from mockazure.jobs.schemas import JobResult
from mockazure.mail.composition import create_message, standard_folder
from mockazure.mail.messages import state_for as mail_state
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.send import (
    DeliveryLeaseExpired,
    actor_data,
    lock_workflow,
    stored_actor,
)
from mockazure.providers.graph.errors import GraphError


async def enqueue_meeting(
    session, actor, box, row, operation, comment, now, *, notify_addresses=None
):
    from mockazure.calendar.exceptions import snapshot_overrides

    identifier = uuid4()
    state = await state_for(session, box)
    job = Job(
        id=identifier,
        tenant_id=actor.tenant_id,
        kind="calendar.meeting",
        payload={"delivery_id": str(identifier)},
        available_at=now,
    )
    session.add(job)
    await session.flush()
    session.add(
        MeetingDelivery(
            id=identifier,
            tenant_id=actor.tenant_id,
            user_id=box.user_id,
            generation=state.generation,
            evidence_generation=(await lock_state(session, box.tenant_id)).generation,
            cloud_generation=(await lock_cloud_state(session, box.tenant_id)).generation,
            actor=actor_data(actor),
            operation=operation,
            event_id=row.id,
            snapshot={
                "event": event_view(row),
                "exceptions": await snapshot_overrides(session, box, row.id),
                "meeting_id": str(row.meeting_id),
                "organizer_id": str(row.organizer_id),
                "revision": row.revision,
                "comment": comment,
                "notify_addresses": notify_addresses,
            },
        )
    )
    await session.flush()


async def local_message(session, actor, sender, target, event, kind, comment, now):
    await mail_state(session, target, create=True)
    folder = await standard_folder(session, target, "inbox")
    payload = {
        "@odata.type": "#microsoft.graph.eventMessage",
        "subject": event.get("subject", ""),
        "body": {"contentType": "text", "content": comment or "Local calendar notification"},
        "meetingMessageType": kind,
        "from": {"emailAddress": {"address": sender.primary_smtp}},
        "sender": {"emailAddress": {"address": sender.primary_smtp}},
        "toRecipients": [{"emailAddress": {"address": target.primary_smtp}}],
        "isDraft": False,
        "receivedDateTime": now.isoformat(),
        "sentDateTime": now.isoformat(),
        "internetMessageId": "<" + uuid4().hex + "@mockazure.test>",
    }
    await create_message(session, target, payload, folder, now)


async def deliver_invitation(session, actor, sender, delivery, now):
    data = delivery.snapshot
    event = data["event"]
    meeting = UUID(data["meeting_id"])
    cancelled = delivery.operation in {"delete", "cancel"}
    targets = {}
    unresolved = []
    for attendee in event.get("attendees", []):
        address = attendee["emailAddress"]["address"]
        try:
            target = await resolve_mailbox(session, actor.tenant_id, address)
        except GraphError as error:
            if error.status != 404:
                raise
            unresolved.append(address)
        else:
            if target.user_id != sender.user_id:
                targets[target.user_id] = target
    # Removed attendees receive a cancellation for their existing local copy.
    previous = list(
        await session.scalars(
            select(CalendarEvent).where(
                CalendarEvent.tenant_id == actor.tenant_id,
                CalendarEvent.meeting_id == meeting,
                CalendarEvent.user_id != sender.user_id,
                CalendarEvent.deleted.is_(False),
            )
        )
    )
    for old in previous:
        if old.user_id not in targets and old.source_revision < data["revision"]:
            try:
                target = await resolve_mailbox(session, actor.tenant_id, str(old.user_id))
            except GraphError:
                continue
            old.source_revision = data["revision"]
            old.payload = {**old.payload, "isCancelled": True}
            await save_event(session, target, old, now)
            await local_message(
                session, actor, sender, target, event, "meetingCancelled", data["comment"], now
            )
    delivered = 0
    for target in sorted(targets.values(), key=lambda b: b.user_id):
        await session.refresh(target, with_for_update=True)
        cal = await calendar_for(session, target)
        key = "AAMk" + uuid5(meeting, str(target.user_id)).hex
        existing = await session.get(CalendarEvent, (actor.tenant_id, target.user_id, key))
        if existing and existing.source_revision >= data["revision"]:
            continue
        payload = {
            k: v
            for k, v in copy.deepcopy(event).items()
            if k not in {"id", "changeKey", "transactionId"}
        }
        response = (
            existing.payload.get("responseStatus")
            if existing
            else {"response": "notResponded", "time": now.isoformat()}
        )
        payload.update(isOrganizer=False, isCancelled=cancelled, responseStatus=response)
        if payload.get("hideAttendees"):
            payload["attendees"] = [
                a
                for a in payload.get("attendees", [])
                if a["emailAddress"]["address"].casefold() == target.primary_smtp
            ]
        if existing is None:
            existing = CalendarEvent(
                tenant_id=actor.tenant_id,
                user_id=target.user_id,
                id=key,
                calendar_id=cal.id,
                meeting_id=meeting,
                organizer_id=sender.user_id,
                revision=0,
                source_revision=data["revision"],
                payload=payload,
                deleted=cancelled,
                transaction_id=None,
                transaction_client=None,
            )
            session.add(existing)
        else:
            existing.payload = payload
            existing.source_revision = data["revision"]
        await save_event(session, target, existing, now)
        from mockazure.calendar.exceptions import restore_overrides

        await restore_overrides(session, target, existing.id, data.get("exceptions", []))
        notify = data.get("notify_addresses")
        if notify is not None and target.primary_smtp not in notify:
            continue
        await local_message(
            session,
            actor,
            sender,
            target,
            event,
            "meetingCancelled"
            if cancelled or delivery.operation == "occurrenceCancel"
            else "meetingRequest",
            data["comment"],
            now,
        )
        delivered += 1
    return {"deliveredCount": delivered, "undeliverableCount": len(unresolved)}


async def deliver_response(session, actor, sender, delivery, now):
    data = delivery.snapshot
    target = await resolve_mailbox(session, actor.tenant_id, data["organizer_id"])
    row = await session.scalar(
        select(CalendarEvent).where(
            CalendarEvent.tenant_id == actor.tenant_id,
            CalendarEvent.user_id == target.user_id,
            CalendarEvent.meeting_id == UUID(data["meeting_id"]),
            CalendarEvent.deleted.is_(False),
        )
    )
    if row is None:
        return {"deliveredCount": 0, "undeliverableCount": 1}
    status = data["event"]["responseStatus"]
    members = copy.deepcopy(row.payload.get("attendees", []))
    matched = False
    for attendee in members:
        if attendee["emailAddress"]["address"].casefold() == sender.primary_smtp:
            old = attendee.get("status", {}).get("time", "")
            if old > status["time"]:
                continue
            attendee["status"] = status
            matched = True
    if not matched:
        return {"deliveredCount": 0, "undeliverableCount": 1}
    row.payload = {**row.payload, "attendees": members}
    await save_event(session, target, row, now)
    kind = {
        "accept": "meetingAccepted",
        "tentativelyAccept": "meetingTentativelyAccepted",
        "decline": "meetingDeclined",
    }[delivery.operation]
    await local_message(session, actor, sender, target, data["event"], kind, data["comment"], now)
    return {"deliveredCount": 1, "undeliverableCount": 0}


async def process_meeting_delivery(database, claim, wall_now):
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    try:
        async with database.sessions.begin() as session:
            await lock_workflow(session, claim.tenant_id)
            job = await lock_claim(session, claim, now())
            if job is None:
                return JobResult("stale", None, None)
            delivery = await session.get(MeetingDelivery, claim.id)
            state = (
                await session.get(CalendarState, (claim.tenant_id, delivery.user_id))
                if delivery
                else None
            )
            if (
                not delivery
                or not state
                or state.generation != delivery.generation
                or (await lock_state(session, claim.tenant_id)).generation
                != delivery.evidence_generation
                or (await lock_cloud_state(session, claim.tenant_id)).generation
                != delivery.cloud_generation
            ):
                job.state, job.error_code = "failed", "MeetingDeliveryInvalidated"
            else:
                actor = stored_actor(delivery.actor)
                sender = await resolve_mailbox(session, claim.tenant_id, str(delivery.user_id))
                await require_calendar_access(session, actor, sender, write=True)
                handler = (
                    deliver_response
                    if delivery.operation in {"accept", "tentativelyAccept", "decline"}
                    else deliver_invitation
                )
                job.result = await handler(session, actor, sender, delivery, now())
                job.state = "succeeded"
            if job.lease_until <= now():
                raise DeliveryLeaseExpired()
            job.lease_owner = job.lease_until = None
            job.updated_at = now()
            return JobResult(job.state, job.result, job.error_code)
    except DeliveryLeaseExpired:
        return JobResult("stale", None, None)
    except (AuthorizationDenied, GraphError):
        async with database.sessions.begin() as session:
            changed = await fail_job(
                session, claim, error_code="MeetingDeliveryInvalidated", now=now(), terminal=True
            )
        return JobResult(
            "failed" if changed else "stale",
            None,
            "MeetingDeliveryInvalidated" if changed else None,
        )
