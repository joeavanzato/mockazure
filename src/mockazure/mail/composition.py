"""Local message construction; bytes remain in the shared PostgreSQL content store."""

import base64
import copy
import hashlib
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from types import SimpleNamespace
from uuid import UUID, uuid4

from mockazure.content.models import CHUNK_SIZE
from mockazure.content.repository import begin_upload, finalize_upload, put_chunk, stream_content
from mockazure.content.stream_upload import (
    append_stream_chunk,
    begin_stream_upload,
    complete_stream_upload,
)
from mockazure.mail.message_models import MailContentFolder, MailMessage
from mockazure.mail.messages import folder_by_id, recount, save_version, state_for
from mockazure.mail.models import MailboxFolder
from mockazure.mail.workflow_contracts import attachment_body, invalid, message_body
from mockazure.providers.graph.errors import GraphError


async def store_bytes(session, tenant, data, media_type):
    identifier = await begin_upload(
        session,
        tenant,
        len(data),
        hashlib.sha256(data).hexdigest(),
        media_type,
        max_size=160 * 1024 * 1024,
    )
    for start in range(0, len(data), CHUNK_SIZE):
        await put_chunk(
            session, tenant, identifier, start // CHUNK_SIZE, data[start : start + CHUNK_SIZE]
        )
    return await finalize_upload(session, tenant, identifier)


async def standard_folder(session, box, name):
    try:
        return await folder_by_id(session, box, name)
    except GraphError as error:
        if error.status != 404:
            raise
    display = {"inbox": "Inbox", "outbox": "Outbox", "drafts": "Drafts", "sentitems": "Sent Items"}[
        name
    ]
    row = MailboxFolder(
        tenant_id=box.tenant_id,
        user_id=box.user_id,
        id="AQMk" + uuid4().hex,
        name=display,
        path="/" + display,
        archive=False,
        item_count=0,
        size_bytes=0,
    )
    session.add(row)
    await session.flush()
    session.add(
        MailContentFolder(
            tenant_id=box.tenant_id,
            user_id=box.user_id,
            id=row.id,
            well_known_name=name,
            is_hidden=False,
        )
    )
    await session.flush()
    return row


def decode_mime(encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
        if len(data) > 6 * 1024 * 1024:
            invalid("MIME exceeds the local request bound")
        mail = BytesParser(policy=policy.default).parsebytes(data)
        if mail.defects or not mail.keys():
            invalid("Invalid MIME content")
        body = {"subject": str(mail.get("Subject", ""))}
        for header, field in [
            ("To", "toRecipients"),
            ("Cc", "ccRecipients"),
            ("Bcc", "bccRecipients"),
            ("Reply-To", "replyTo"),
        ]:
            body[field] = [
                {"emailAddress": {"name": name, "address": address}}
                for name, address in getaddresses(mail.get_all(header, []))
            ]
        for header, field in [("From", "from"), ("Sender", "sender")]:
            if mail.get(header):
                addresses = getaddresses(mail.get_all(header, []))
                if len(addresses) != 1:
                    invalid("One sending identity is required")
                name, address = addresses[0]
                body[field] = {"emailAddress": {"name": name, "address": address}}
        text = mail.get_body(preferencelist=("html", "plain"))
        body["body"] = {
            "contentType": "html" if text and text.get_content_subtype() == "html" else "text",
            "content": text.get_content() if text else "",
        }
        if mail.get("Message-ID"):
            body["internetMessageId"] = str(mail["Message-ID"])
        body["internetMessageHeaders"] = [
            {"name": name, "value": str(value)}
            for name, value in mail.items()
            if name.lower().startswith("x-")
        ]
        attachments = []
        for part in mail.iter_attachments():
            if part.is_multipart():
                invalid("Nested MIME attachments are outside this profile")
            raw = part.get_payload(decode=True)
            attachments.append(
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": part.get_filename() or "attachment",
                    "contentType": part.get_content_type(),
                    "isInline": part.get_content_disposition() == "inline",
                    "contentBytes": base64.b64encode(raw).decode(),
                }
            )
        body["attachments"] = attachments
        return message_body(body)
    except (ValueError, UnicodeError, TypeError, LookupError):
        invalid("Invalid base64 string for MIME content", "ErrorMimeContentInvalidBase64String")


async def attachments_from_json(session, tenant, values):
    result = []
    for value in values:
        data = attachment_body(value)
        kind = value["@odata.type"].removeprefix("#microsoft.graph.")
        payload = {
            k: v for k, v in value.items() if k not in {"@odata.type", "contentBytes", "item"}
        }
        record = {"id": "AAMk" + uuid4().hex, "kind": kind, "payload": payload}
        if kind == "itemAttachment":
            item = copy.deepcopy(value["item"])
            ref = await render_mime(
                session,
                SimpleNamespace(tenant_id=tenant),
                SimpleNamespace(payload=item, attachments=[]),
            )
            if ref.size >= 3 * 1024 * 1024:
                invalid("Item attachments must be smaller than 3 MiB")
            payload.update(size=ref.size, contentType="message/rfc822")
            record.update(item=item, content_id=str(ref.id))
        elif kind == "fileAttachment":
            media = value.get("contentType", "application/octet-stream")
            ref = await store_bytes(session, tenant, data, media)
            payload.update(size=len(data), contentType=media)
            record["content_id"] = str(ref.id)
        else:
            payload["size"] = 0
        result.append(record)
    return result


async def render_mime(session, box, row):
    mail = EmailMessage(policy=policy.SMTP)
    payload = row.payload
    mail["Subject"] = payload.get("subject", "")
    for field, header in [("from", "From"), ("sender", "Sender")]:
        if payload.get(field):
            mail[header] = payload[field]["emailAddress"]["address"]
    for field, header in [("toRecipients", "To"), ("ccRecipients", "Cc"), ("replyTo", "Reply-To")]:
        if payload.get(field):
            mail[header] = ", ".join(x["emailAddress"]["address"] for x in payload[field])
    if payload.get("internetMessageId"):
        mail["Message-ID"] = payload["internetMessageId"]
    for h in payload.get("internetMessageHeaders", []):
        mail[h["name"]] = h["value"]
    body = payload.get("body") or {"contentType": "text", "content": ""}
    mail.set_content(
        body.get("content", ""), subtype="html" if body.get("contentType") == "html" else "plain"
    )
    if not row.attachments:
        return await store_bytes(session, box.tenant_id, mail.as_bytes(), "message/rfc822")
    boundary = "mockazure_" + uuid4().hex
    mail.make_mixed(boundary=boundary)
    raw = mail.as_bytes()
    end = ("--" + boundary + "--\r\n").encode()
    assert raw.endswith(end)
    upload = await begin_stream_upload(session, box.tenant_id, None, 240 * 1024 * 1024)
    await append_stream_chunk(session, box.tenant_id, upload, raw[: -len(end)])
    for attachment in row.attachments:
        if not attachment.get("content_id"):
            continue
        part = EmailMessage(policy=policy.SMTP)
        meta = attachment["payload"]
        part["Content-Type"] = meta.get("contentType", "application/octet-stream")
        nested_message = attachment["kind"] == "itemAttachment"
        part["Content-Transfer-Encoding"] = "8bit" if nested_message else "base64"
        part.set_payload("")
        part.add_header(
            "Content-Disposition",
            "inline" if meta.get("isInline") else "attachment",
            filename=meta.get("name", "attachment"),
        )
        if meta.get("contentId"):
            part["Content-ID"] = meta["contentId"]
        await append_stream_chunk(
            session, box.tenant_id, upload, ("--" + boundary + "\r\n").encode() + part.as_bytes()
        )
        carry = b""
        async for chunk in stream_content(session, box.tenant_id, UUID(attachment["content_id"])):
            if nested_message:
                await append_stream_chunk(session, box.tenant_id, upload, chunk)
                continue
            chunk = carry + chunk
            size = len(chunk) // 57 * 57
            encoded = base64.encodebytes(chunk[:size]).replace(b"\n", b"\r\n")
            if encoded:
                await append_stream_chunk(session, box.tenant_id, upload, encoded)
            carry = chunk[size:]
        if nested_message:
            await append_stream_chunk(session, box.tenant_id, upload, b"\r\n")
        if carry:
            await append_stream_chunk(
                session, box.tenant_id, upload, base64.b64encode(carry) + b"\r\n"
            )
    await append_stream_chunk(session, box.tenant_id, upload, end)
    return await complete_stream_upload(session, box.tenant_id, upload, "message/rfc822")


async def changed_message(session, box, row, now, *, deleted=False):
    state = await state_for(session, box, create=True)
    state.revision += 1
    row.revision = state.revision
    row.payload = {**row.payload, "lastModifiedDateTime": now.isoformat()}
    if not deleted:
        ref = await render_mime(session, box, row)
        row.mime_content_id, row.size_bytes = ref.id, ref.size
    await session.flush()
    await save_version(session, row, state.revision, deleted=deleted)
    if deleted:
        await session.delete(row)
    await session.flush()
    await recount(session, box)
    return row


async def create_message(session, box, body, folder, now, *, attachments=None):
    body = copy.deepcopy(body)
    values = body.pop("attachments", [])
    stored = await attachments_from_json(session, box.tenant_id, values)
    if attachments:
        stored = copy.deepcopy(attachments) + stored
    default = {
        "subject": "",
        "body": {"contentType": "text", "content": ""},
        "isRead": False,
        "isDraft": True,
        "categories": [],
        "createdDateTime": now.isoformat(),
        "conversationId": "AAQk" + uuid4().hex,
    }
    row = MailMessage(
        tenant_id=box.tenant_id,
        user_id=box.user_id,
        immutable_id=uuid4(),
        id="AAMk" + uuid4().hex,
        folder_id=folder.id,
        revision=0,
        payload=default | body,
        attachments=stored,
        size_bytes=0,
    )
    session.add(row)
    return await changed_message(session, box, row, now)
