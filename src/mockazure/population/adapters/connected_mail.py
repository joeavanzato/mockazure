"""Historical mail MIME and attachments; no send, delivery, or native-operation facts."""

import hashlib
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime

from pydantic import Field, model_validator
from sqlalchemy import select

from mockazure.content.models import ContentObject
from mockazure.mail.content_models import AttachmentImport, MessageImport
from mockazure.population.corpus.models import ContentSpec, RenderedContent
from mockazure.population.corpus.render import render
from mockazure.population.corpus.storage import store_rendered
from mockazure.population.types import Input


class ConnectedMessage(Input):
    message: MessageImport
    document: ContentSpec
    attachment_id: str = Field(default="project-document", min_length=1, max_length=128)

    @model_validator(mode="after")
    def mime_contract(self):
        payload = self.message.payload
        for name in ("subject", "internetMessageId"):
            value = payload.get(name)
            if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
                raise ValueError("Connected mail requires bounded single-line headers")
        stamp = payload.get("sentDateTime")
        if (
            not isinstance(stamp, str)
            or datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None
        ):
            raise ValueError("Connected mail requires an offset-aware sentDateTime")
        recipients = payload.get("toRecipients")
        if not isinstance(recipients, list) or not 1 <= len(recipients) <= 500:
            raise ValueError("Connected mail requires 1 to 500 recipients")
        for address in [payload.get("from"), *recipients]:
            text = ((address or {}).get("emailAddress") or {}).get("address")
            if not isinstance(text, str) or "@" not in text or any(ord(c) < 32 for c in text):
                raise ValueError("Connected mail requires single-line email addresses")
        body = payload.get("body") or {}
        if body.get("contentType") != "text" or not isinstance(body.get("content"), str):
            raise ValueError("Connected history MIME currently requires a text body")
        if self.message.mime_content_id is not None:
            raise ValueError("Connected MIME is generated, not supplied by content ID")
        for header in payload.get("internetMessageHeaders") or []:
            name, value = header.get("name"), header.get("value")
            if not isinstance(name, str) or not isinstance(value, str) or any(
                ord(c) < 32 for c in name + value
            ):
                raise ValueError("Connected mail headers must be single-line text")
        render(self.document)
        return self


def mime_bytes(value, document):
    payload = value.message.payload
    message = EmailMessage(policy=policy.SMTP)
    message["Subject"] = payload["subject"]
    message["From"] = payload["from"]["emailAddress"]["address"]
    message["To"] = ", ".join(v["emailAddress"]["address"] for v in payload["toRecipients"])
    message["Date"] = format_datetime(
        datetime.fromisoformat(payload["sentDateTime"].replace("Z", "+00:00"))
    )
    message["Message-ID"] = payload["internetMessageId"]
    for header in payload.get("internetMessageHeaders") or []:
        if header["name"].casefold() in {"in-reply-to", "references"}:
            message[header["name"]] = header["value"]
    message.set_content(payload["body"]["content"], subtype="plain")
    maintype, subtype = document.media_type.split("/", 1)
    message.add_attachment(
        b"".join(document.chunks()), maintype=maintype, subtype=subtype, filename=document.filename
    )
    message.set_boundary(
        "mockazure-"
        + hashlib.sha256((payload["internetMessageId"] + document.sha256).encode()).hexdigest()
    )
    return message.as_bytes()


async def materialize(session, ctx, value):
    document = render(value.document)
    content = await store_rendered(
        session, ctx.tenant_id, document, ctx.settings.content_max_object_bytes
    )
    mime = mime_bytes(value, document)
    mime_content = RenderedContent(
        "message/rfc822",
        "message.eml",
        len(mime),
        hashlib.sha256(mime).hexdigest(),
        lambda: (mime[start : start + 1024 * 1024] for start in range(0, len(mime), 1024 * 1024)),
    )
    mime_ref = await store_rendered(
        session, ctx.tenant_id, mime_content, ctx.settings.content_max_object_bytes
    )
    message = value.message.model_copy(
        update={"mime_content_id": mime_ref.id, "size_bytes": len(mime)}
    )
    attachment = AttachmentImport(
        message_id=message.id,
        id=value.attachment_id,
        kind="fileAttachment",
        content_id=content.id,
        payload={
            "name": document.filename,
            "contentType": document.media_type,
            "size": document.length,
            "isInline": False,
        },
    )
    return message, attachment


async def matches_document(session, tenant, attachments, value):
    document = render(value.document)
    if len(attachments) != 1 or attachments[0]["id"] != value.attachment_id:
        return False
    row = await session.scalar(
        select(ContentObject).where(
            ContentObject.tenant_id == tenant,
            ContentObject.id == attachments[0]["content_id"],
            ContentObject.state == "finalized",
        )
    )
    return row is not None and (row.sha256, row.size) == (document.sha256, document.length)
