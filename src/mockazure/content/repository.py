"""Transactional chunks; callers authorize resources before accessing binary evidence."""

import hashlib
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, exists, select, text

from mockazure.admin.errors import AdminError
from mockazure.content.models import (
    CHUNK_SIZE,
    DEFAULT_MAX_SIZE,
    ContentChunk,
    ContentObject,
    ContentRef,
    ContentReference,
)
from mockazure.db.models import Tenant


def fail(status, code, message):
    raise AdminError(status, code, message)


def reference(row):
    return ContentRef(row.id, row.size, row.sha256, row.media_type)


async def object_row(session, tenant_id, content_id, *, lock=False, finalized=False):
    query = (
        select(ContentObject)
        .where(ContentObject.tenant_id == tenant_id, ContentObject.id == content_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        query = query.with_for_update(read=lock == "read")
    row = await session.scalar(query)
    if row is None or (finalized and row.state != "finalized"):
        fail(404, "ContentNotFound", "Finalized content was not found")
    return row


async def begin_upload(
    session, tenant_id: UUID, size: int, sha256: str, media_type: str, *, max_size=DEFAULT_MAX_SIZE
) -> UUID:
    if type(size) is not int or not 0 <= size <= max_size:
        fail(422, "InvalidContentSize", "Content exceeds the configured size limit")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
        fail(422, "InvalidContentHash", "A SHA-256 digest is required")
    if (
        not isinstance(media_type, str)
        or not re.fullmatch(r"[A-Za-z0-9!#$&^_.+*-]+/[A-Za-z0-9!#$&^_.+*-]+", media_type)
        or len(media_type) > 255
    ):
        fail(422, "InvalidMediaType", "A simple MIME media type is required")
    if await session.get(Tenant, tenant_id) is None:
        fail(404, "TenantNotFound", "Tenant not found")
    row = ContentObject(
        tenant_id=tenant_id, size=size, sha256=sha256.lower(), media_type=media_type.lower()
    )
    session.add(row)
    await session.flush()
    return row.id


async def put_chunk(session, tenant_id: UUID, upload_id: UUID, ordinal: int, data: bytes) -> None:
    row = await object_row(session, tenant_id, upload_id, lock=True)
    if row.state != "staged":
        fail(409, "UploadComplete", "This upload is already complete")
    expected = (
        min(CHUNK_SIZE, row.size - ordinal * CHUNK_SIZE)
        if type(ordinal) is int and ordinal >= 0
        else -1
    )
    if not isinstance(data, bytes) or not 0 < expected <= CHUNK_SIZE or len(data) != expected:
        fail(422, "InvalidChunk", "Chunk ordinal or size does not match the declared object")
    current = await session.get(ContentChunk, (tenant_id, upload_id, ordinal))
    if current is not None:
        if current.data != data:
            fail(409, "ChunkConflict", "This ordinal already contains different bytes")
        return
    session.add(ContentChunk(tenant_id=tenant_id, content_id=upload_id, ordinal=ordinal, data=data))
    await session.flush()


async def finalize_upload(session, tenant_id: UUID, upload_id: UUID) -> ContentRef:
    row = await object_row(session, tenant_id, upload_id)
    # Stable hash lock serializes only competing deduplications, across API instances.
    lock_key = int.from_bytes(
        hashlib.sha256((str(tenant_id) + row.sha256 + row.media_type).encode()).digest()[:8],
        "big",
        signed=True,
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    row = await object_row(session, tenant_id, upload_id, lock=True)
    if row.state == "alias":
        canonical = await object_row(
            session, tenant_id, row.canonical_id, lock=True, finalized=True
        )
        # created_at is the internal retention age, not an exported evidence timestamp.
        row.created_at = canonical.created_at = datetime.now(UTC)
        await session.flush()
        return reference(canonical)
    if row.state == "finalized":
        row.created_at = datetime.now(UTC)
        await session.flush()
        return reference(row)
    digest, total, ordinal = hashlib.sha256(), 0, 0
    chunks = await session.stream(
        select(ContentChunk.ordinal, ContentChunk.data)
        .where(ContentChunk.tenant_id == tenant_id, ContentChunk.content_id == upload_id)
        .order_by(ContentChunk.ordinal)
        .execution_options(yield_per=1)
    )
    async for index, data in chunks:
        if index != ordinal:
            fail(422, "IncompleteUpload", "Upload has missing chunks")
        digest.update(data)
        total += len(data)
        ordinal += 1
    if total != row.size or digest.hexdigest() != row.sha256:
        fail(422, "ContentMismatch", "Uploaded size or SHA-256 does not match")
    canonical = await session.scalar(
        select(ContentObject)
        .where(
            ContentObject.tenant_id == tenant_id,
            ContentObject.sha256 == row.sha256,
            ContentObject.media_type == row.media_type,
            ContentObject.state == "finalized",
        )
        .with_for_update()
    )
    row.created_at = datetime.now(UTC)
    if canonical is not None:
        canonical.created_at = row.created_at
        row.state, row.canonical_id = "alias", canonical.id
        await session.execute(
            delete(ContentChunk).where(
                ContentChunk.tenant_id == tenant_id, ContentChunk.content_id == upload_id
            )
        )
        await session.flush()
        return reference(canonical)
    row.state = "finalized"
    await session.flush()
    return reference(row)


async def stream_content(
    session, tenant_id: UUID, content_id: UUID, start: int = 0, end: int | None = None
):
    row = await object_row(session, tenant_id, content_id, lock="read", finalized=True)
    end = row.size if end is None else end
    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= row.size:
        fail(416, "InvalidRange", "Requested byte range is not satisfiable")
    if start == end:
        return
    chunks = await session.stream(
        select(ContentChunk.ordinal, ContentChunk.data)
        .where(
            ContentChunk.tenant_id == tenant_id,
            ContentChunk.content_id == content_id,
            ContentChunk.ordinal >= start // CHUNK_SIZE,
            ContentChunk.ordinal <= (end - 1) // CHUNK_SIZE,
        )
        .order_by(ContentChunk.ordinal)
        .execution_options(yield_per=1)
    )
    async for ordinal, data in chunks:
        offset = ordinal * CHUNK_SIZE
        yield data[max(0, start - offset) : min(len(data), end - offset)]


async def pin_content(session, tenant_id, content_id, owner_kind, owner_id):
    await object_row(session, tenant_id, content_id, lock=True, finalized=True)
    if (
        not isinstance(owner_kind, str)
        or not 1 <= len(owner_kind) <= 64
        or not isinstance(owner_id, str)
        or not 1 <= len(owner_id) <= 512
        or "\0" in owner_kind + owner_id
    ):
        fail(422, "InvalidReference", "Content reference owner is invalid")
    key = (tenant_id, content_id, owner_kind, owner_id)
    if await session.get(ContentReference, key) is None:
        session.add(
            ContentReference(
                tenant_id=tenant_id, content_id=content_id, owner_kind=owner_kind, owner_id=owner_id
            )
        )
        await session.flush()


async def release_content(session, tenant_id, owner_kind, owner_id):
    await session.execute(
        delete(ContentReference).where(
            ContentReference.tenant_id == tenant_id,
            ContentReference.owner_kind == owner_kind,
            ContentReference.owner_id == owner_id,
        )
    )


async def export_manifest(session, tenant_id):
    rows = await session.scalars(
        select(ContentObject)
        .where(ContentObject.tenant_id == tenant_id, ContentObject.state == "finalized")
        .order_by(ContentObject.id)
    )
    return [{**asdict(reference(row)), "id": str(row.id)} for row in rows]


async def validate_manifest(session, tenant_id, manifest, mapping=None):
    mapping = mapping or {}
    identifiers = [str(item["id"]) for item in manifest]
    if len(set(identifiers)) != len(identifiers) or set(mapping) - set(identifiers):
        fail(422, "InvalidManifest", "Duplicate content IDs or unknown mapping keys")
    for item in manifest:
        target = mapping.get(str(item["id"]), str(item["id"]))
        row = await object_row(session, tenant_id, UUID(str(target)), lock="read", finalized=True)
        if any(item[key] != getattr(row, key) for key in ("size", "sha256", "media_type")):
            fail(422, "ManifestMismatch", "Content manifest does not match finalized bytes")


async def prune_content(session, tenant_id, now, limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Cleanup limit must be 1..1000")
    pinned = exists(
        select(1).where(
            ContentReference.tenant_id == ContentObject.tenant_id,
            ContentReference.content_id == ContentObject.id,
        )
    )
    criteria = (
        ContentObject.tenant_id == tenant_id,
        ContentObject.created_at < now - timedelta(days=1),
        ~pinned,
    )
    rows = list(
        await session.scalars(
            select(ContentObject)
            .where(*criteria)
            .order_by(ContentObject.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    deleted = 0
    for row in rows:
        # Recheck after the row lock: concurrent pinning holds this same lock.
        if await session.scalar(
            select(
                exists(
                    select(1).where(
                        ContentReference.tenant_id == tenant_id,
                        ContentReference.content_id == row.id,
                    )
                )
            )
        ):
            continue
        await session.delete(row)
        deleted += 1
    await session.flush()
    remaining = bool(await session.scalar(select(exists(select(1).where(*criteria)))))
    return {"deleted": deleted, "remaining": remaining}


async def reset_content(session, tenant_id):
    tenant = await session.get(Tenant, tenant_id, with_for_update=True)
    if tenant is None:
        fail(404, "TenantNotFound", "Tenant not found")
    # Lock all candidates before rechecking references; pinning uses the same rows.
    await session.execute(
        select(ContentObject.id)
        .where(ContentObject.tenant_id == tenant_id)
        .order_by(ContentObject.id)
        .with_for_update()
    )
    if await session.scalar(
        select(exists(select(1).where(ContentReference.tenant_id == tenant_id)))
    ):
        fail(
            409, "ContentInUse", "Clear referencing workload state before resetting binary content"
        )
    result = await session.execute(
        delete(ContentObject).where(ContentObject.tenant_id == tenant_id)
    )
    return {"deleted": result.rowcount}
