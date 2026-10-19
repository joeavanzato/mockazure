"""Mailbox-serialized content mutations and transactional native operation facts."""

import copy
from uuid import uuid4

from mockazure.identity.models import User
from mockazure.mail.facts import record_mail_fact
from mockazure.mail.message_models import MailMessage
from mockazure.mail.messages import folder_by_id, recount, save_version, state_for
from mockazure.providers.graph.errors import GraphError


def fact_view(row):
    return {
        "id": row.id,
        "parentFolderId": row.folder_id,
        "isRead": row.payload.get("isRead", False),
        "categories": row.payload.get("categories", []),
    }


async def mutate_message(session, actor, box, row, operation, payload, now):
    state = await state_for(session, box)
    if state is None:
        raise GraphError(409, "ErrorInvalidOperation", "Mailbox has no imported content")
    before = fact_view(row)
    destination = None
    if operation in {"move", "copy"}:
        if set(payload) != {"destinationId"} or not isinstance(payload["destinationId"], str):
            raise GraphError(400, "Request_BadRequest", "A destinationId string is required")
        destination = await folder_by_id(session, box, payload["destinationId"])
    elif operation == "update":
        from mockazure.mail.workflow_contracts import message_body

        if not payload:
            raise GraphError(400, "Request_BadRequest", "An update body is required")
        payload = message_body(payload, update=True, is_draft=row.payload.get("isDraft", False))
        if payload.keys() & {"from", "sender"}:
            # The field can be prepared on a draft, but send authority is checked
            # separately at acceptance and again by the delivery worker.
            if not row.payload.get("isDraft"):
                raise GraphError(400, "ErrorInvalidOperation", "Sending identity requires a draft")
        if all(row.payload.get(k) == v for k, v in payload.items()):
            return row
    elif payload:
        raise GraphError(400, "Request_BadRequest", "Delete has no request body")
    state.revision += 1
    revision = state.revision
    if operation == "copy":
        row = MailMessage(
            tenant_id=box.tenant_id,
            user_id=box.user_id,
            immutable_id=uuid4(),
            id="AAMk" + uuid4().hex,
            folder_id=destination.id,
            revision=revision,
            payload=copy.deepcopy(row.payload),
            mime_content_id=row.mime_content_id,
            size_bytes=row.size_bytes,
            attachments=copy.deepcopy(row.attachments),
        )
        before = {}
        session.add(row)
    elif operation == "move":
        row.folder_id = destination.id
        row.id = "AAMk" + uuid4().hex
    elif operation == "update":
        row.payload = {**row.payload, **payload}
    row.revision = revision
    if operation != "delete":
        row.payload = {**row.payload, "lastModifiedDateTime": now.isoformat()}
        if operation == "update" and row.payload.get("isDraft"):
            from mockazure.mail.composition import render_mime

            content = await render_mime(session, box, row)
            row.mime_content_id, row.size_bytes = content.id, content.size
        await session.flush()
    await save_version(session, row, revision, deleted=operation == "delete")
    after = {} if operation == "delete" else fact_view(row)
    if operation == "delete":
        await session.delete(row)
    await session.flush()
    await recount(session, box)
    user = await session.get(User, box.user_id)
    await record_mail_fact(
        session,
        actor,
        {"move": "Move", "copy": "Copy", "delete": "SoftDelete", "update": "Update"}[operation],
        {"id": str(box.user_id), "upn": user.upn, "primarySmtpAddress": box.primary_smtp},
        before,
        after,
        now,
    )
    return row
