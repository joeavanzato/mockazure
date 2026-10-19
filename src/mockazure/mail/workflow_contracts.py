"""Graph v1.0 mail action semantics over Microsoft's pinned structural types."""

import base64
import copy
import json
from functools import lru_cache

from mockazure.cloud.contracts import DATA
from mockazure.providers.graph.body import action_parameters, typed_body
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload

MAX_INLINE = 3 * 1024 * 1024
MAX_ATTACHMENT = 150 * 1024 * 1024
MESSAGE_FIELDS = frozenset(
    {
        "subject",
        "body",
        "toRecipients",
        "ccRecipients",
        "bccRecipients",
        "replyTo",
        "from",
        "sender",
        "importance",
        "isDeliveryReceiptRequested",
        "isReadReceiptRequested",
        "categories",
        "internetMessageHeaders",
        "internetMessageId",
        "isRead",
        "flag",
    }
)
DRAFT_ONLY = frozenset(
    {
        "subject",
        "body",
        "toRecipients",
        "ccRecipients",
        "bccRecipients",
        "replyTo",
        "internetMessageId",
    }
)
UPDATE_FIELDS = MESSAGE_FIELDS - {"internetMessageHeaders"} | {"inferenceClassification"}


def invalid(message="Invalid Graph mail action body", code="Request_BadRequest"):
    raise GraphError(400, code, message)


@lru_cache
def references():
    return json.loads((DATA / "graph-workflows/schema.json").read_text("utf-8"))


def recipient_address(value):
    if not isinstance(value, dict) or not isinstance(value.get("emailAddress"), dict):
        invalid("A recipient emailAddress is required")
    address = value["emailAddress"].get("address")
    if (
        not isinstance(address, str)
        or address.count("@") != 1
        or any(c.isspace() for c in address)
        or len(address) > 320
    ):
        invalid("A valid bounded recipient address is required")
    return address.casefold()


def attachment_body(value):
    if not isinstance(value, dict) or not isinstance(value.get("@odata.type"), str):
        invalid("A typed attachment is required")
    kind = value["@odata.type"].removeprefix("#microsoft.graph.")
    if kind not in {"fileAttachment", "itemAttachment", "referenceAttachment"}:
        invalid("Unknown attachment type")
    fields = {"@odata.type", "name", "contentType", "isInline"}
    fields |= (
        {"contentId", "contentLocation", "contentBytes"}
        if kind == "fileAttachment"
        else {"item"}
        if kind == "itemAttachment"
        else set()
    )
    if value.keys() - fields:
        invalid("Attachment property is not writable")
    try:
        validate_payload(
            "v1.0",
            kind,
            {k: v for k, v in value.items() if k not in {"contentBytes", "item"}},
            partial=True,
        )
    except ValueError:
        invalid("Invalid attachment schema")
    if not isinstance(value.get("name"), str) or not value["name"]:
        invalid("Attachment name is required")
    for field in ("name", "contentType", "contentId", "contentLocation"):
        if field in value and (
            not isinstance(value[field], str) or any(c in value[field] for c in "\r\n")
        ):
            invalid("Attachment header values must be single-line strings")
    if kind == "referenceAttachment":
        return None
    if kind == "itemAttachment":
        item = value.get("item")
        if (
            not isinstance(item, dict)
            or item.get("@odata.type") != "#microsoft.graph.message"
            or "attachments" in item
        ):
            raise GraphError(
                501, "NotSupported", "Item writes support a message without nested attachments"
            )
        message_body(item)
        return None
    raw = value.get("contentBytes")
    if not isinstance(raw, str) or len(raw) >= 4 * MAX_INLINE // 3 + 4:
        invalid("Use an upload session for attachments of 3 MiB or larger")
    try:
        data = base64.b64decode(raw, validate=True)
    except ValueError:
        invalid("Invalid attachment base64 bytes")
    if len(data) >= MAX_INLINE:
        invalid("Use an upload session for attachments of 3 MiB or larger")
    return data


def message_body(value, *, update=False, is_draft=True):
    value = typed_body(value, "message")
    allowed = UPDATE_FIELDS if update else MESSAGE_FIELDS | {"attachments"}
    if value.keys() - allowed or (update and not is_draft and value.keys() & DRAFT_ONLY):
        invalid("Message property is not writable in its current state")
    structural = {k: v for k, v in value.items() if k != "attachments"}
    try:
        validate_payload("v1.0", "message", structural, partial=True)
    except ValueError:
        invalid("Message does not match the Graph v1.0 schema")
    for field in ("subject", "internetMessageId"):
        if field in value and (value[field] is None or any(c in value[field] for c in "\r\n")):
            invalid("Message header values must be single-line strings")
    count = 0
    for field in ("toRecipients", "ccRecipients", "bccRecipients", "replyTo"):
        if field not in value:
            continue
        if not isinstance(value[field], list):
            invalid("Recipients must be an array")
        count += len(value[field])
        for recipient in value[field]:
            recipient_address(recipient)
    if count > 500:
        invalid("Local recipient limit exceeded")
    for field in ("from", "sender"):
        if field in value:
            recipient_address(value[field])
    if "attachments" in value:
        if not isinstance(value["attachments"], list) or len(value["attachments"]) > 100:
            invalid("Local attachment count limit exceeded")
        for attachment in value["attachments"]:
            attachment_body(attachment)
    for header in value.get("internetMessageHeaders", []):
        if (
            not isinstance(header.get("name"), str)
            or not header["name"].lower().startswith("x-")
            or any(c in header.get("value", "") for c in "\r\n")
        ):
            invalid("Custom Internet headers must use x- names and single-line values")
    return value


def validate_mail_action(action, body):
    if not isinstance(body, dict):
        invalid()
    body = copy.deepcopy(body)
    if action in {"create", "update"}:
        return message_body(body, update=action == "update")
    if action == "addAttachment":
        attachment_body(body)
        return body
    if action in {"send", "deleteAttachment"}:
        if body:
            invalid("This operation takes no body")
        return body
    if action == "createUploadSession":
        if set(body) != {"AttachmentItem"}:
            invalid("AttachmentItem is required")
        item = body["AttachmentItem"]
        fields = references()["types"]["attachmentItem"]["properties"]
        if not isinstance(item, dict) or item.keys() - fields.keys():
            invalid()
        if (
            item.get("attachmentType") != "file"
            or not isinstance(item.get("name"), str)
            or not item["name"]
            or len(item["name"]) > 256
        ):
            invalid("A named file attachment is required")
        if type(item.get("size")) is not int or not MAX_INLINE <= item["size"] <= MAX_ATTACHMENT:
            invalid("Upload sessions support file attachments from 3 to 150 MiB")
        if "isInline" in item and type(item["isInline"]) is not bool:
            invalid()
        for field in ("name", "contentId", "contentType"):
            if field in item and (
                not isinstance(item[field], str)
                or len(item[field]) > 256
                or any(c in item[field] for c in "\r\n\0")
            ):
                invalid()
        return body
    if action not in {
        "sendMail",
        "reply",
        "replyAll",
        "forward",
        "createReply",
        "createReplyAll",
        "createForward",
    }:
        invalid("Unknown mail action")
    allowed = (
        {"message", "saveToSentItems"}
        if action == "sendMail"
        else {"message", "comment"}
        | ({"toRecipients"} if action in {"forward", "createForward"} else set())
    )
    body = action_parameters(body, allowed)
    if action == "sendMail" and "message" not in body:
        invalid("message is required")
    if "message" in body:
        body["message"] = message_body(body["message"])
    if "saveToSentItems" in body and type(body["saveToSentItems"]) is not bool:
        invalid("saveToSentItems must be a boolean")
    if "comment" in body:
        if not isinstance(body["comment"], str) or len(body["comment"]) > 16384:
            invalid()
        if "body" in body.get("message", {}):
            invalid("Specify comment or message.body, not both")
    if "toRecipients" in body:
        if "toRecipients" in body.get("message", {}):
            invalid("Specify recipients only once")
        if not isinstance(body["toRecipients"], list) or len(body["toRecipients"]) > 500:
            invalid()
        for recipient in body["toRecipients"]:
            recipient_address(recipient)
    return body
