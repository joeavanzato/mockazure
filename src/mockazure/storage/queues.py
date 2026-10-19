"""Queue delivery transactions use current authorization and database row locks."""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select

from mockazure.cloud.models import CloudState
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.containers import check_name, record
from mockazure.storage.models import StorageNamespace
from mockazure.storage.queue_models import QueueCursor, QueueMessage


def invalid(message, code="InvalidQueryParameterValue"):
    raise StorageError(400, code, message)


def date(value):
    return format_datetime(value.astimezone(UTC), usegmt=True)


def checked_text(text):
    if not isinstance(text, str) or any(
        not (
            c in "\t\r\n"
            or 32 <= ord(c) <= 0xD7FF
            or 0xE000 <= ord(c) <= 0xFFFD
            or 0x10000 <= ord(c) <= 0x10FFFF
        )
        for c in text
    ):
        invalid("MessageText must be XML-compatible text", "InvalidXmlDocument")
    if len(text.encode("utf-8")) > 65536:
        invalid("Message exceeds 64 KiB", "MessageTooLarge")
    return text


def later(now, seconds):
    try:
        return now + timedelta(seconds=seconds)
    except OverflowError:
        invalid("Time exceeds local datetime range")


def rotate(row):
    receipt = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    row.receipt_hash = hashlib.sha256(receipt.encode()).hexdigest()
    return receipt


def message_view(row, receipt=None, *, text=True):
    result = {
        "MessageId": str(row.id),
        "InsertionTime": date(row.inserted_at),
        "ExpirationTime": date(row.expires_at or datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)),
    }
    if text:
        result.update(MessageText=row.body, DequeueCount=str(row.dequeue_count))
    if receipt:
        result.update(PopReceipt=receipt, TimeNextVisible=date(row.visible_at))
    return result


async def find_queue(session, access, queue):
    check_name(queue)
    row = await session.get(StorageNamespace, (access.tenant_id, access.account_id, "queue", queue))
    if row is None:
        raise StorageError(404, "QueueNotFound", "The specified queue does not exist")
    return row


async def create_queue(session, access, queue, metadata, now):
    check_name(queue)
    row = await session.get(StorageNamespace, (access.tenant_id, access.account_id, "queue", queue))
    if row:
        if row.properties.get("metadata", {}) != metadata:
            raise StorageError(
                409, "QueueAlreadyExists", "The queue exists with different metadata"
            )
        return 204
    session.add(
        StorageNamespace(
            tenant_id=access.tenant_id,
            account_id=access.account_id,
            service="queue",
            name=queue,
            etag='"' + uuid4().hex + '"',
            created_at=now,
            changed_at=now,
            properties={"metadata": metadata},
            policies=[],
        )
    )
    await session.flush()
    await record(session, access, "queue", queue, "CreateQueue", now)
    return 201


def selection(access, queue, now=None):
    query = select(QueueMessage).where(
        QueueMessage.tenant_id == access.tenant_id,
        QueueMessage.account_id == access.account_id,
        QueueMessage.queue == queue,
    )
    if now is not None:
        query = query.where(or_(QueueMessage.expires_at.is_(None), QueueMessage.expires_at > now))
    return query


async def enqueue_message(session, access, queue, text, ttl, visibility_seconds, now):
    await find_queue(session, access, queue)
    checked_text(text)
    if (
        ttl != -1
        and ttl < 1
        or not 0 <= visibility_seconds <= 604800
        or ttl != -1
        and visibility_seconds >= ttl
    ):
        invalid("Invalid message TTL or visibility interval")
    row = QueueMessage(
        tenant_id=access.tenant_id,
        account_id=access.account_id,
        queue=queue,
        body=text,
        inserted_at=now,
        expires_at=None if ttl == -1 else later(now, ttl),
        visible_at=later(now, visibility_seconds),
        dequeue_count=0,
    )
    receipt = rotate(row)
    session.add(row)
    await session.flush()
    await record(session, access, "queue", queue, "PutMessage", now)
    return message_view(row, receipt, text=False)


async def peek_messages(session, access, queue, count, now):
    await find_queue(session, access, queue)
    if not 1 <= count <= 32:
        invalid("numofmessages must be between 1 and 32")
    rows = await session.scalars(
        selection(access, queue, now)
        .where(QueueMessage.visible_at <= now)
        .order_by(QueueMessage.inserted_at, QueueMessage.id)
        .limit(count)
    )
    return [message_view(row) for row in rows]


async def receive_messages(session, access, queue, count, visibility_seconds, now):
    await find_queue(session, access, queue)
    if not 1 <= count <= 32 or not 1 <= visibility_seconds <= 604800:
        invalid("Invalid receive count or visibility interval")
    rows = list(
        await session.scalars(
            selection(access, queue, now)
            .where(QueueMessage.visible_at <= now)
            .order_by(QueueMessage.inserted_at, QueueMessage.id)
            .limit(count)
            .with_for_update(skip_locked=True)
        )
    )
    result = []
    for row in rows:
        row.dequeue_count += 1
        row.visible_at = later(now, visibility_seconds)
        result.append(message_view(row, rotate(row)))
    if rows:
        await record(session, access, "queue", queue, "GetMessages", now)
    await session.flush()
    return result


async def receipt_message(session, access, queue, message_id, pop_receipt, now):
    await find_queue(session, access, queue)
    try:
        identifier = UUID(message_id)
    except ValueError:
        identifier = None
    row = (
        await session.scalar(
            selection(access, queue, now).where(QueueMessage.id == identifier).with_for_update()
        )
        if identifier
        else None
    )
    if row is None:
        raise StorageError(404, "MessageNotFound", "The specified message does not exist")
    if not row.receipt_hash or not secrets.compare_digest(
        row.receipt_hash, hashlib.sha256(pop_receipt.encode()).hexdigest()
    ):
        invalid("The pop receipt does not match the current message", "PopReceiptMismatch")
    return row


async def update_message(
    session, access, queue, message_id, pop_receipt, text, visibility_seconds, now
):
    row = await receipt_message(session, access, queue, message_id, pop_receipt, now)
    if not 0 <= visibility_seconds <= 604800:
        invalid("Invalid visibility interval")
    visible = later(now, visibility_seconds)
    if row.expires_at and visible > row.expires_at:
        invalid("Visibility interval exceeds message expiration")
    if text is not None:
        row.body = checked_text(text)
    row.visible_at = visible
    receipt = rotate(row)
    await record(session, access, "queue", queue, "UpdateMessage", now)
    await session.flush()
    return {"x-ms-popreceipt": receipt, "x-ms-time-next-visible": date(visible)}


async def delete_message(session, access, queue, message_id, pop_receipt, now):
    row = await receipt_message(session, access, queue, message_id, pop_receipt, now)
    await session.delete(row)
    await record(session, access, "queue", queue, "DeleteMessage", now)


async def clear_messages(session, access, queue, now):
    await find_queue(session, access, queue)
    await session.execute(
        delete(QueueMessage).where(
            QueueMessage.tenant_id == access.tenant_id,
            QueueMessage.account_id == access.account_id,
            QueueMessage.queue == queue,
        )
    )
    await record(session, access, "queue", queue, "ClearMessages", now)


async def message_count(session, access, queue, now):
    return await session.scalar(
        select(func.count()).select_from(selection(access, queue, now).subquery())
    )


async def list_queues(session, access, args, now):
    maximum = int(args.get("maxresults", "5000"))
    if maximum < 1:
        invalid("maxresults must be positive")
    maximum = min(maximum, 5000)
    if args.get("include", "") not in {"", "metadata"}:
        invalid("Only metadata inclusion is supported")
    query = {
        "prefix": args.get("prefix", ""),
        "maxresults": maximum,
        "include": args.get("include", ""),
    }
    generation = (await session.get(CloudState, access.tenant_id)).generation
    last = None
    if args.get("marker"):
        try:
            identifier = UUID(args["marker"])
        except ValueError:
            invalid("Invalid marker", "InvalidMarker")
        cursor = await session.get(QueueCursor, (access.tenant_id, identifier))
        if (
            cursor is None
            or cursor.account_id != access.account_id
            or cursor.generation != generation
            or cursor.query != query
            or cursor.expires_at <= now
        ):
            invalid("Expired or mismatched marker", "InvalidMarker")
        last = cursor.last_key
    sql = select(StorageNamespace).where(
        StorageNamespace.tenant_id == access.tenant_id,
        StorageNamespace.account_id == access.account_id,
        StorageNamespace.service == "queue",
        StorageNamespace.name.startswith(query["prefix"], autoescape=True),
    )
    if last:
        sql = sql.where(StorageNamespace.name > last)
    rows = list(await session.scalars(sql.order_by(StorageNamespace.name).limit(maximum + 1)))
    marker = ""
    if len(rows) > maximum:
        cursor = QueueCursor(
            tenant_id=access.tenant_id,
            account_id=access.account_id,
            generation=generation,
            query=query,
            last_key=rows[maximum - 1].name,
            expires_at=now + timedelta(hours=1),
        )
        session.add(cursor)
        await session.flush()
        marker = str(cursor.id)
    return rows[:maximum], marker
