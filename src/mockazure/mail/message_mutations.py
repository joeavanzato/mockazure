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
        if not payload or payload.keys() - {"isRead", "categories"}:
            raise GraphError(400, "Request_BadRequest", "Only isRead and categories can be updated")
        if "isRead" in payload and type(payload["isRead"]) is not bool:
            raise GraphError(400, "Request_BadRequest", "isRead must be a boolean")
        if "categories" in payload and (
            not isinstance(payload["categories"], list)
            or len(payload["categories"]) > 100
            or any(not isinstance(v, str) or len(v) > 256 for v in payload["categories"])
        ):
            raise GraphError(400, "Request_BadRequest", "Invalid bounded categories array")
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
