"""Sequential Outlook attachment uploads with expiring capability authority."""

import hashlib
import re
import secrets
from datetime import timedelta
from uuid import uuid4

from mockazure.cloud.events import lock_cloud_state
from mockazure.content.stream_upload import (
    append_stream_chunk,
    begin_stream_upload,
    complete_stream_upload,
)
from mockazure.evidence.repository import lock_state
from mockazure.mail.authorization import require_mailbox_access
from mockazure.mail.composition import changed_message
from mockazure.mail.message_models import MailMessage
from mockazure.mail.messages import state_for
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.send import actor_data, current_stored_actor, lock_workflow, mail_fact
from mockazure.mail.send_models import MailAttachmentUpload
from mockazure.providers.graph.errors import GraphError


def not_found():
    raise GraphError(404, "ErrorItemNotFound", "Upload session was not found or expired")


def upload_view(row, url):
    return {
        "uploadUrl": url,
        "expirationDateTime": row.expires_at.isoformat(),
        "nextExpectedRanges": [str(row.received)],
    }


async def create_attachment_upload(session, actor, box, row, item, origin, now):
    if not row.payload.get("isDraft"):
        raise GraphError(400, "ErrorInvalidOperation", "Attachment uploads require a draft")
    state = await state_for(session, box, create=True)
    token = secrets.token_urlsafe(32)
    content = await begin_stream_upload(session, box.tenant_id, item["size"], 150 * 1024 * 1024)
    upload = MailAttachmentUpload(
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        tenant_id=box.tenant_id,
        user_id=box.user_id,
        message_id=row.immutable_id,
        generation=state.generation,
        evidence_generation=(await lock_state(session, actor.tenant_id)).generation,
        cloud_generation=(await lock_cloud_state(session, actor.tenant_id)).generation,
        content_id=content,
        expected=item["size"],
        received=0,
        item=item,
        actor=actor_data(actor),
        expires_at=now + timedelta(hours=1),
    )
    session.add(upload)
    await session.flush()
    return upload_view(upload, origin + "/_upload/mail/" + token)


async def use_attachment_upload(session, token, method, body, content_range, origin, now):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        not_found()
    upload = await session.get(MailAttachmentUpload, hashlib.sha256(token.encode()).hexdigest())
    if upload is None:
        not_found()
    await lock_workflow(session, upload.tenant_id)
    await session.refresh(upload, with_for_update=True)
    if upload.expires_at <= now:
        not_found()
    box = await resolve_mailbox(session, upload.tenant_id, str(upload.user_id))
    await session.refresh(box, with_for_update=True)
    state = await state_for(session, box)
    row = await session.get(MailMessage, (upload.tenant_id, upload.user_id, upload.message_id))
    evidence = await lock_state(session, upload.tenant_id)
    cloud = await lock_cloud_state(session, upload.tenant_id)
    if (
        not state
        or state.generation != upload.generation
        or row is None
        or evidence.generation != upload.evidence_generation
        or cloud.generation != upload.cloud_generation
    ):
        not_found()
    actor = await current_stored_actor(session, upload.actor)
    await require_mailbox_access(session, actor, box, "content.write")
    if not row.payload.get("isDraft"):
        raise GraphError(400, "ErrorInvalidOperation", "The upload target is no longer a draft")
    if method == "DELETE":
        await session.delete(upload)
        return 204, None, {}
    if method == "GET":
        return 200, upload_view(upload, origin + "/_upload/mail/" + token), {}
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range or "")
    if not match:
        raise GraphError(400, "ErrorInvalidRequest", "A valid Content-Range is required")
    start, end, total = map(int, match.groups())
    if (
        start != upload.received
        or end < start
        or end - start + 1 != len(body)
        or total != upload.expected
        or end >= total
    ):
        raise GraphError(416, "ErrorInvalidRange", "Range does not match the next expected bytes")
    if len(body) > 4 * 1024 * 1024:
        raise GraphError(413, "ErrorMessageSizeExceeded", "Upload ranges cannot exceed 4 MiB")
    await append_stream_chunk(session, upload.tenant_id, upload.content_id, body)
    upload.received = end + 1
    if upload.received < upload.expected:
        return 200, upload_view(upload, origin + "/_upload/mail/" + token), {}
    item = upload.item
    ref = await complete_stream_upload(
        session,
        upload.tenant_id,
        upload.content_id,
        item.get("contentType", "application/octet-stream"),
    )
    identifier = "AAMk" + uuid4().hex
    payload = {k: v for k, v in item.items() if k != "attachmentType"}
    payload.setdefault("contentType", "application/octet-stream")
    row.attachments = [
        *row.attachments,
        {"id": identifier, "kind": "fileAttachment", "content_id": str(ref.id), "payload": payload},
    ]
    await changed_message(session, box, row, now)
    await mail_fact(session, actor, box, "Update", row, now)
    await session.delete(upload)
    return (
        201,
        None,
        {
            "Location": origin
            + f"/api/v2.0/Users('{box.user_id}')/Messages('{row.id}')/Attachments('{identifier}')"
        },
    )
