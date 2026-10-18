"""Immutable Blob content and transactionally published service facts."""

import base64
import copy
import hashlib
from datetime import UTC, timedelta
from email.utils import parsedate_to_datetime
from uuid import uuid4

from sqlalchemy import delete, select

from mockazure.content.models import DEFAULT_MAX_SIZE
from mockazure.content.repository import pin_content, release_content, stream_content
from mockazure.content.stream_upload import (
    append_stream_chunk,
    begin_stream_upload,
    complete_stream_upload,
)
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.blob_models import Blob, BlobBlock, BlobLease, BlobVersion
from mockazure.storage.containers import record
from mockazure.storage.leases import require_lease
from mockazure.storage.models import StorageAccount, StorageNamespace
from mockazure.storage.types import ContentVersion


def name_hash(name):
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 1024
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise StorageError(400, "InvalidResourceName", "Invalid Blob name")
    return hashlib.sha256(name.encode()).hexdigest()


async def find_blob(session, access, container, name, now, *, create=False):
    if (
        await session.get(
            StorageNamespace, (access.tenant_id, access.account_id, "blob", container)
        )
        is None
    ):
        raise StorageError(404, "ContainerNotFound", "Container was not found")
    digest = name_hash(name)
    blob = await session.scalar(
        select(Blob)
        .where(
            Blob.tenant_id == access.tenant_id,
            Blob.account_id == access.account_id,
            Blob.container == container,
            Blob.name_hash == digest,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if blob is not None and blob.name != name:
        raise StorageError(409, "BlobNameConflict", "Blob name hash conflict")
    if blob is None and create:
        blob = Blob(
            tenant_id=access.tenant_id,
            account_id=access.account_id,
            container=container,
            name=name,
            name_hash=digest,
            changed_at=now,
        )
        session.add(blob)
        await session.flush()
    return blob


async def selected_version(session, blob, selector=None, kind="version", *, deleted=False):
    if blob is None:
        return None
    if selector:
        row = await session.scalar(
            select(BlobVersion).where(
                BlobVersion.tenant_id == blob.tenant_id,
                BlobVersion.blob_id == blob.id,
                BlobVersion.stamp == selector,
                BlobVersion.kind == kind,
            )
        )
    else:
        row = (
            await session.get(BlobVersion, (blob.tenant_id, blob.current_id))
            if blob.current_id
            else None
        )
    return row if row is not None and (deleted or row.deleted_at is None) else None


def check_conditions(version, values, *, read=False):
    etag = version.etag if version else None

    def matches(raw):
        return version is not None and (raw == "*" or etag in [p.strip() for p in raw.split(",")])

    if "if-match" in values and not matches(values["if-match"]):
        raise StorageError(412, "ConditionNotMet", "ETag precondition failed")
    modified = version.created_at.astimezone(UTC).replace(microsecond=0) if version else None
    dates = {}
    for key in ("if-modified-since", "if-unmodified-since"):
        if key in values:
            try:
                date = parsedate_to_datetime(values[key])
                if date.tzinfo is None:
                    raise ValueError()
                dates[key] = date
            except (ValueError, TypeError):
                raise StorageError(400, "InvalidHeaderValue", "Invalid condition date") from None
    if modified and "if-unmodified-since" in dates and modified > dates["if-unmodified-since"]:
        raise StorageError(412, "ConditionNotMet", "Modification-time precondition failed")
    none_match = "if-none-match" not in values or not matches(values["if-none-match"])
    changed = "if-modified-since" not in dates or (
        modified is not None and modified > dates["if-modified-since"]
    )
    passed = (
        (none_match or changed)
        if read and "if-none-match" in values and "if-modified-since" in dates
        else (none_match and changed)
    )
    if not passed:
        raise StorageError(304 if read else 412, "ConditionNotMet", "Content precondition failed")


async def profile(session, access):
    account = await session.get(StorageAccount, (access.tenant_id, access.account_id))
    return account.services.get("blob", {})


async def new_version(session, blob, content, properties, blocks, now, *, kind="version"):
    stamp_time = now.astimezone(UTC)
    while True:
        stamp = stamp_time.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"
        exists = await session.scalar(
            select(BlobVersion.id).where(
                BlobVersion.tenant_id == blob.tenant_id,
                BlobVersion.blob_id == blob.id,
                BlobVersion.stamp == stamp,
                BlobVersion.kind == kind,
            )
        )
        if exists is None:
            break
        stamp_time += timedelta(microseconds=1)
    row = BlobVersion(
        tenant_id=blob.tenant_id,
        id=uuid4(),
        blob_id=blob.id,
        content_id=content.id,
        stamp=stamp,
        kind=kind,
        etag='"' + uuid4().hex + '"',
        size=content.size,
        created_at=now,
        properties=copy.deepcopy(properties),
        blocks=copy.deepcopy(blocks),
    )
    session.add(row)
    await session.flush()
    await pin_content(session, blob.tenant_id, content.id, "blob-version", str(row.id))
    return row


async def put_blob(session, access, container, name, upload_id, conditions, now) -> ContentVersion:
    blob = await find_blob(session, access, container, name, now, create=True)
    old = await selected_version(session, blob)
    check_conditions(old, conditions)
    await require_lease(session, blob, conditions.get("x-ms-lease-id"), now, write=True)
    properties = conditions.get("properties", {})
    ref = await complete_stream_upload(
        session,
        access.tenant_id,
        upload_id,
        properties.get("content_type", "application/octet-stream"),
    )
    settings = await profile(session, access)
    if old is not None and not settings.get("isVersioningEnabled", False):
        retention = settings.get("deleteRetentionPolicy", {})
        if retention.get("enabled"):
            old.kind = "snapshot"
            old.deleted_at = now
            old.deleted_until = now + timedelta(days=retention["days"])
        else:
            await session.delete(old)
    row = await new_version(session, blob, ref, properties, conditions.get("blocks", []), now)
    blob.current_id = row.id
    blob.changed_at = now
    lease = await session.get(BlobLease, (blob.tenant_id, blob.id))
    if lease:
        lease.etag = row.etag
    await record(
        session, access, "blob", container + "/" + name, conditions.get("operation", "PutBlob"), now
    )
    await session.flush()
    return ContentVersion(row.id, row.content_id, row.etag, row.size)


def block_identifier(value):
    try:
        decoded = base64.b64decode(value, validate=True)
        if not 1 <= len(decoded) <= 64:
            raise ValueError()
    except (ValueError, TypeError):
        raise StorageError(400, "InvalidBlobOrBlock", "Invalid block identifier") from None
    return decoded


async def stage_block(session, access, container, name, block_id, upload_id, conditions, now):
    decoded = block_identifier(block_id)
    blob = await find_blob(session, access, container, name, now, create=True)
    await require_lease(session, blob, conditions.get("x-ms-lease-id"), now, write=True)
    old = await selected_version(session, blob)
    candidates = list(
        await session.scalars(
            select(BlobBlock.block_id).where(
                BlobBlock.tenant_id == access.tenant_id, BlobBlock.blob_id == blob.id
            )
        )
    )
    candidates += [item["id"] for item in old.blocks] if old else []
    if any(len(block_identifier(v)) != len(decoded) for v in candidates):
        raise StorageError(
            400, "InvalidBlobOrBlock", "Block identifiers must have equal decoded lengths"
        )
    if len(set(candidates)) >= 100000 and block_id not in candidates:
        raise StorageError(409, "BlockCountExceedsLimit", "Too many uncommitted blocks")
    ref = await complete_stream_upload(
        session, access.tenant_id, upload_id, "application/octet-stream"
    )
    row = await session.scalar(
        select(BlobBlock).where(
            BlobBlock.tenant_id == access.tenant_id,
            BlobBlock.blob_id == blob.id,
            BlobBlock.block_id == block_id,
        )
    )
    if row:
        await release_content(session, access.tenant_id, "blob-block", str(row.id))
        row.content_id = ref.id
        row.size = ref.size
        row.created_at = now
    else:
        row = BlobBlock(
            tenant_id=access.tenant_id,
            id=uuid4(),
            blob_id=blob.id,
            block_id=block_id,
            content_id=ref.id,
            size=ref.size,
            created_at=now,
        )
        session.add(row)
    await session.flush()
    await pin_content(session, access.tenant_id, ref.id, "blob-block", str(row.id))
    await record(session, access, "blob", container + "/" + name, "PutBlock", now)


async def commit_blocks(session, access, container, name, entries, conditions, now):
    if len(entries) > 50000:
        raise StorageError(400, "BlockCountExceedsLimit", "Too many committed blocks")
    blob = await find_blob(session, access, container, name, now, create=True)
    current = await selected_version(session, blob)
    check_conditions(current, conditions)
    await require_lease(session, blob, conditions.get("x-ms-lease-id"), now, write=True)
    committed = {item["id"]: item for item in current.blocks} if current else {}
    staged = {
        v.block_id: v
        for v in await session.scalars(
            select(BlobBlock).where(
                BlobBlock.tenant_id == access.tenant_id, BlobBlock.blob_id == blob.id
            )
        )
    }
    sources = []
    blocks = []
    size = 0
    for kind, identifier in entries:
        block_identifier(identifier)
        if kind in {"Latest", "Uncommitted"} and identifier in staged:
            value = staged[identifier]
            content_id = value.content_id
            start = 0
            length = value.size
        elif kind in {"Latest", "Committed"} and identifier in committed:
            value = committed[identifier]
            content_id = current.content_id
            start = value["offset"]
            length = value["size"]
        else:
            raise StorageError(400, "InvalidBlockList", "A selected block was not found")
        sources.append((content_id, start, start + length))
        blocks.append({"id": identifier, "size": length, "offset": size})
        size += length
    upload = await begin_stream_upload(session, access.tenant_id, size, DEFAULT_MAX_SIZE)
    for content_id, start, end in sources:
        async for data in stream_content(session, access.tenant_id, content_id, start, end):
            await append_stream_chunk(session, access.tenant_id, upload, data)
    result = await put_blob(
        session,
        access,
        container,
        name,
        upload,
        conditions | {"blocks": blocks, "operation": "PutBlockList"},
        now,
    )
    await session.execute(
        delete(BlobBlock).where(
            BlobBlock.tenant_id == access.tenant_id, BlobBlock.blob_id == blob.id
        )
    )
    return result
