"""Accepted Graph sends become fenced, transactional local mailbox deliveries."""

import copy
from dataclasses import asdict, replace
from datetime import timedelta
from html import escape
from time import monotonic
from uuid import UUID, uuid4

from sqlalchemy import or_, select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.cloud.events import lock_cloud_state
from mockazure.cloud.types import MutationResult
from mockazure.db.job_model import Job
from mockazure.evidence.repository import lock_state
from mockazure.identity.models import Grant, User
from mockazure.identity.types import PrincipalContext
from mockazure.jobs.repository import fail_job, lock_claim
from mockazure.jobs.schemas import JobResult
from mockazure.mail.authorization import require_mailbox_access
from mockazure.mail.composition import changed_message, create_message, decode_mime, standard_folder
from mockazure.mail.delegation import lock_delegation, require_send_identity
from mockazure.mail.facts import record_mail_fact
from mockazure.mail.message_models import MailMessage
from mockazure.mail.message_queries import message_view
from mockazure.mail.messages import folder_by_id, message_by_id, state_for
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.send_models import MailDelivery
from mockazure.mail.workflow_contracts import invalid, recipient_address, validate_mail_action
from mockazure.providers.graph.errors import GraphError
from mockazure.security.repository import lock_security


async def lock_workflow(session, tenant):
    await lock_delegation(session, tenant)
    await lock_security(session, tenant)
    await lock_state(session, tenant)
    await lock_cloud_state(session, tenant)


def actor_data(actor):
    return {
        k: sorted(v) if isinstance(v, frozenset) else str(v) if isinstance(v, UUID) else v
        for k, v in asdict(actor).items()
    }


def stored_actor(data):
    return PrincipalContext(
        **{
            k: UUID(v)
            if k in {"tenant_id", "client_id", "subject_id", "session_id"} and v
            else frozenset(v)
            if k == "permissions"
            else v
            for k, v in data.items()
        }
    )


async def current_stored_actor(session, data):
    """Saved workflow authority is bounded by consent still present under the lock."""
    actor = stored_actor(data)
    query = select(Grant).where(
        Grant.tenant_id == actor.tenant_id,
        Grant.client_id == actor.client_id,
        Grant.resource == "graph",
        Grant.kind == actor.token_kind,
    )
    query = query.where(
        or_(Grant.user_id.is_(None), Grant.user_id == actor.subject_id)
        if actor.token_kind == "delegated"
        else Grant.user_id.is_(None)
    )
    grants = await session.scalars(query)
    permissions = frozenset(permission for grant in grants for permission in grant.permissions)
    return replace(actor, permissions=actor.permissions & permissions)


def forwarded_body(original, commentary):
    original = original or {"contentType": "text", "content": ""}
    if commentary is None:
        return copy.deepcopy(original)
    html = any(b.get("contentType", "text").lower() == "html" for b in (original, commentary))
    if not html:
        return {
            "contentType": "text",
            "content": commentary.get("content", "") + "\n\n" + original.get("content", ""),
        }

    def content(body):
        value = body.get("content", "")
        return (
            value
            if body.get("contentType", "text").lower() == "html"
            else escape(value).replace("\n", "<br>")
        )

    return {
        "contentType": "html",
        "content": "<div>"
        + content(commentary)
        + "</div><blockquote>"
        + content(original)
        + "</blockquote>",
    }


def recipient(box):
    return {"emailAddress": {"address": box.primary_smtp}}


async def identity_for_send(session, actor, box, payload):
    represented = (
        await resolve_mailbox(session, actor.tenant_id, recipient_address(payload["from"]))
        if payload.get("from")
        else box
    )
    mode = "sendAs"
    sender = (
        recipient_address(payload["sender"]) if payload.get("sender") else represented.primary_smtp
    )
    if sender != represented.primary_smtp:
        if actor.token_kind != "delegated":
            raise AuthorizationDenied("Application sender mismatch")
        actual = await resolve_mailbox(session, actor.tenant_id, str(actor.subject_id))
        if sender != actual.primary_smtp:
            raise AuthorizationDenied("Sender does not match current actor")
        mode = "sendOnBehalf"
    await require_send_identity(session, actor, box.user_id, represented.user_id, mode)
    payload["from"] = recipient(represented)
    payload["sender"] = {"emailAddress": {"address": sender}}
    return represented, mode


async def mail_fact(session, actor, box, operation, row, now):
    user = await session.get(User, box.user_id)
    await record_mail_fact(
        session,
        actor,
        operation,
        {"id": str(box.user_id), "upn": user.upn, "primarySmtpAddress": box.primary_smtp},
        {},
        {"id": row.id, "parentFolderId": row.folder_id},
        now,
    )


def reply_body(source, box, action, body):
    result = copy.deepcopy(body.get("message", {}))
    forward = action in {"forward", "createForward"}
    result.setdefault(
        "subject", ("FW: " if forward else "RE: ") + source.payload.get("subject", "")
    )
    result.setdefault("conversationId", source.payload.get("conversationId", "AAQk" + uuid4().hex))
    if "comment" in body:
        result["body"] = {"contentType": "text", "content": body["comment"]}
    if not forward and source.payload.get("internetMessageId"):
        parent = source.payload["internetMessageId"]
        previous = next(
            (
                h["value"]
                for h in source.payload.get("internetMessageHeaders", [])
                if h["name"].lower() == "references"
            ),
            "",
        )
        result["internetMessageHeaders"] = [
            *result.get("internetMessageHeaders", []),
            {"name": "In-Reply-To", "value": parent},
            {"name": "References", "value": " ".join([*previous.split()[-20:], parent])},
        ]
    if forward:
        result["body"] = forwarded_body(source.payload.get("body"), result.get("body"))
        if "toRecipients" in body:
            result["toRecipients"] = body["toRecipients"]
    else:
        targets = source.payload.get("replyTo") or [source.payload.get("from")]
        if action in {"replyAll", "createReplyAll"}:
            targets = (
                targets
                + source.payload.get("toRecipients", [])
                + source.payload.get("ccRecipients", [])
            )
        seen = {box.primary_smtp}
        recipients = []
        for target in targets:
            if not target:
                continue
            address = recipient_address(target)
            if address not in seen:
                recipients.append(copy.deepcopy(target))
                seen.add(address)
        result.setdefault("toRecipients", recipients)
    return result


async def accept_mail_action(
    session, actor, mailbox_id, action, message_id, body, now, *, folder_id=None
):
    await lock_workflow(session, actor.tenant_id)
    box = await resolve_mailbox(session, actor.tenant_id, str(mailbox_id))
    await session.refresh(box, with_for_update=True)
    sending = action in {"sendMail", "send", "reply", "replyAll", "forward"}
    if not sending:
        await require_mailbox_access(session, actor, box, "content.write")
    if isinstance(body, bytes):
        body = (
            {"message": decode_mime(body)}
            if action not in {"create", "update"}
            else decode_mime(body)
        )
    body = validate_mail_action(action, body)
    source = await message_by_id(session, box, message_id) if message_id else None
    attachments = None
    if action in {"reply", "replyAll", "forward", "createReply", "createReplyAll", "createForward"}:
        if source is None:
            invalid("Source message is required")
        payload = reply_body(source, box, action, body)
        if action in {"forward", "createForward"}:
            attachments = source.attachments
    elif action == "send":
        if source is None or not source.payload.get("isDraft"):
            raise GraphError(400, "ErrorInvalidOperation", "Only an unsent draft can be sent")
        payload = copy.deepcopy(source.payload)
    else:
        payload = copy.deepcopy(body["message"] if action == "sendMail" else body)
    represented = mode = None
    if sending:
        represented, mode = await identity_for_send(session, actor, box, payload)
        if not any(payload.get(k) for k in ("toRecipients", "ccRecipients", "bccRecipients")):
            invalid("At least one recipient is required", "ErrorInvalidRecipients")
    else:
        payload.setdefault("from", recipient(box))
        payload.setdefault("sender", recipient(box))
    folder = (
        await folder_by_id(session, box, folder_id)
        if action == "create" and folder_id
        else await standard_folder(session, box, "outbox" if sending else "drafts")
    )
    await state_for(session, box, create=True)
    payload["isDraft"] = not sending
    payload.setdefault("internetMessageId", "<" + uuid4().hex + "@mockazure.test>")
    if action == "send":
        source.payload = payload
        source.folder_id = folder.id
        row = await changed_message(session, box, source, now)
    else:
        row = await create_message(session, box, payload, folder, now, attachments=attachments)
    if sending:
        state = await state_for(session, box)
        identifier = uuid4()
        job = Job(
            id=identifier,
            tenant_id=actor.tenant_id,
            kind="mail.delivery",
            payload={"delivery_id": str(identifier)},
            available_at=now,
        )
        session.add(job)
        await session.flush()
        session.add(
            MailDelivery(
                id=identifier,
                tenant_id=actor.tenant_id,
                user_id=box.user_id,
                message_id=row.immutable_id,
                generation=state.generation,
                evidence_generation=(await lock_state(session, actor.tenant_id)).generation,
                cloud_generation=(await lock_cloud_state(session, actor.tenant_id)).generation,
                actor=actor_data(actor),
                represented_id=represented.user_id,
                mode=mode,
                save_sent=body.get("saveToSentItems", True),
            )
        )
        await session.flush()
        return MutationResult(202, None, {})
    await mail_fact(session, actor, box, "Create", row, now)
    return MutationResult(
        201, message_view(row, {"immutable": False, "select": None, "body": "html"}), {}
    )


class DeliveryLeaseExpired(Exception):
    pass


async def process_mail_delivery(database, claim, wall_now):
    started = monotonic()

    def now():
        return wall_now + timedelta(seconds=monotonic() - started)

    try:
        async with database.sessions.begin() as session:
            await lock_workflow(session, claim.tenant_id)
            job = await lock_claim(session, claim, now())
            if job is None:
                return JobResult("stale", None, None)
            delivery = await session.get(MailDelivery, claim.id)
            if delivery is None or delivery.tenant_id != claim.tenant_id:
                job.state = "failed"
                job.error_code = "MailDeliveryInvalidated"
            else:
                box = await resolve_mailbox(session, claim.tenant_id, str(delivery.user_id))
                state = await state_for(session, box)
                row = await session.get(
                    MailMessage, (claim.tenant_id, box.user_id, delivery.message_id)
                )
                actor = await current_stored_actor(session, delivery.actor)
                evidence = await lock_state(session, claim.tenant_id)
                cloud = await lock_cloud_state(session, claim.tenant_id)
                if (
                    not state
                    or state.generation != delivery.generation
                    or row is None
                    or evidence.generation != delivery.evidence_generation
                    or cloud.generation != delivery.cloud_generation
                ):
                    job.state = "failed"
                    job.error_code = "MailDeliveryInvalidated"
                else:
                    try:
                        await require_send_identity(
                            session, actor, box.user_id, delivery.represented_id, delivery.mode
                        )
                    except AuthorizationDenied:
                        job.state = "failed"
                        job.error_code = "MailDeliveryAuthorizationRevoked"
                    else:
                        # Tenant workflow lock serializes recipient resolution and mailbox writes.
                        targets = {}
                        unresolved = []
                        for field in ("toRecipients", "ccRecipients", "bccRecipients"):
                            for target in row.payload.get(field, []):
                                address = recipient_address(target)
                                try:
                                    target_box = await resolve_mailbox(
                                        session, claim.tenant_id, address
                                    )
                                    targets[target_box.user_id] = target_box
                                except GraphError as error:
                                    if error.status != 404:
                                        raise
                                    unresolved.append(address)
                        delivered = []
                        for target in sorted(targets.values(), key=lambda item: item.user_id):
                            await session.refresh(target, with_for_update=True)
                            await state_for(session, target, create=True)
                            inbox = await standard_folder(session, target, "inbox")
                            payload = copy.deepcopy(row.payload)
                            payload.pop("bccRecipients", None)
                            payload.update(
                                isDraft=False,
                                isRead=False,
                                receivedDateTime=now().isoformat(),
                                sentDateTime=now().isoformat(),
                            )
                            await create_message(
                                session, target, payload, inbox, now(), attachments=row.attachments
                            )
                            delivered.append(str(target.user_id))
                        await session.refresh(box, with_for_update=True)
                        row.payload = {
                            **row.payload,
                            "sentDateTime": now().isoformat(),
                            "isDraft": False,
                        }
                        if delivery.save_sent:
                            sent = await standard_folder(session, box, "sentitems")
                            row.folder_id = sent.id
                            row.id = "AAMk" + uuid4().hex
                        await changed_message(
                            session, box, row, now(), deleted=not delivery.save_sent
                        )
                        await mail_fact(
                            session,
                            actor,
                            box,
                            "SendOnBehalf"
                            if delivery.mode == "sendOnBehalf"
                            else "SendAs"
                            if actor.token_kind == "delegated"
                            and actor.subject_id != delivery.represented_id
                            else "Send",
                            row,
                            now(),
                        )
                        delivery.outcome = {
                            "delivered": delivered,
                            "undeliverableLocalRecipients": sorted(set(unresolved)),
                        }
                        job.state = "succeeded"
                        job.result = {
                            "deliveredCount": len(delivered),
                            "undeliverableCount": len(set(unresolved)),
                        }
            if job.lease_until <= now():
                raise DeliveryLeaseExpired()
            job.lease_owner = job.lease_until = None
            job.updated_at = now()
            return JobResult(job.state, job.result, job.error_code)
    except DeliveryLeaseExpired:
        return JobResult("stale", None, None)

    except GraphError:
        # A deleted or invalid mailbox is a terminal local outcome; roll back
        # the delivery transaction before recording a fenced job failure.
        async with database.sessions.begin() as session:
            changed = await fail_job(
                session, claim, error_code="MailDeliveryInvalidated", now=now(), terminal=True
            )
        return JobResult(
            "failed" if changed else "stale", None, "MailDeliveryInvalidated" if changed else None
        )
