"""Bounded PostgreSQL streaming adapter; Azure clients need no prior SHA-256."""

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from mockazure.cloud.events import lock_cloud_state
from mockazure.content.models import CHUNK_SIZE, ContentChunk, StreamUpload
from mockazure.content.repository import begin_upload, finalize_upload, object_row
from mockazure.providers.storage.errors import StorageError


async def begin_stream_upload(
    session, tenant_id: UUID, expected_length: int | None, max_size: int
) -> UUID:
    if (
        type(max_size) is not int
        or max_size < 0
        or (
            expected_length is not None
            and (type(expected_length) is not int or not 0 <= expected_length <= max_size)
        )
    ):
        raise StorageError(413, "RequestBodyTooLarge", "Upload exceeds the local content limit")
    state = await lock_cloud_state(session, tenant_id)
    identifier = await begin_upload(
        session, tenant_id, 0, "0" * 64, "application/octet-stream", max_size=max_size
    )
    session.add(
        StreamUpload(
            tenant_id=tenant_id,
            id=identifier,
            expected_length=expected_length,
            max_size=max_size,
            generation=state.generation,
        )
    )
    await session.flush()
    return identifier


async def staging(session, tenant_id, upload_id):
    # Reset and append serialize on the same generation before touching staging rows.
    state = await lock_cloud_state(session, tenant_id)
    row = await session.scalar(
        select(StreamUpload)
        .where(StreamUpload.tenant_id == tenant_id, StreamUpload.id == upload_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or state is None or row.generation != state.generation:
        raise StorageError(
            409, "InvalidUpload", "This upload no longer belongs to the current cloud state"
        )
    content = await object_row(session, tenant_id, upload_id, lock=True)
    if content.state != "staged":
        raise StorageError(409, "UploadComplete", "The stream upload is already complete")
    return row, content


async def append_stream_chunk(session, tenant_id: UUID, upload_id: UUID, data: bytes) -> None:
    row, content = await staging(session, tenant_id, upload_id)
    if not isinstance(data, bytes):
        raise TypeError("Stream chunks must be bytes")
    limit = row.max_size if row.expected_length is None else row.expected_length
    if content.size + len(data) > limit:
        raise StorageError(413, "RequestBodyTooLarge", "Upload exceeds its declared or local limit")
    view = memoryview(data)
    while view:
        ordinal = content.size // CHUNK_SIZE
        offset = content.size % CHUNK_SIZE
        length = min(len(view), CHUNK_SIZE - offset)
        chunk = await session.get(ContentChunk, (tenant_id, upload_id, ordinal)) if offset else None
        if offset and chunk is None:
            raise StorageError(409, "InvalidUpload", "Stream staging is incomplete")
        if chunk is None:
            session.add(
                ContentChunk(
                    tenant_id=tenant_id,
                    content_id=upload_id,
                    ordinal=ordinal,
                    data=bytes(view[:length]),
                )
            )
        else:
            chunk.data = chunk.data + bytes(view[:length])
        content.size += length
        view = view[length:]
        await session.flush()


async def complete_stream_upload(session, tenant_id: UUID, upload_id: UUID, media_type: str):
    row, content = await staging(session, tenant_id, upload_id)
    if row.expected_length is not None and content.size != row.expected_length:
        raise StorageError(
            400, "InvalidHeaderValue", "Content length does not match uploaded bytes"
        )
    digest = hashlib.sha256()
    total = 0
    ordinal = 0
    async for index, data in await session.stream(
        select(ContentChunk.ordinal, ContentChunk.data)
        .where(ContentChunk.tenant_id == tenant_id, ContentChunk.content_id == upload_id)
        .order_by(ContentChunk.ordinal)
        .execution_options(yield_per=1)
    ):
        if index != ordinal or (len(data) != CHUNK_SIZE and total + len(data) != content.size):
            raise StorageError(409, "InvalidUpload", "Stream chunk layout is incomplete")
        digest.update(data)
        total += len(data)
        ordinal += 1
    if total != content.size:
        raise StorageError(409, "InvalidUpload", "Stream staging is incomplete")
    if (
        not isinstance(media_type, str)
        or len(media_type) > 255
        or any(ord(c) < 32 for c in media_type)
    ):
        raise StorageError(400, "InvalidHeaderValue", "Invalid content type")
    content.sha256 = digest.hexdigest()
    content.media_type = media_type
    content.created_at = datetime.now(UTC)
    await session.flush()
    return await finalize_upload(session, tenant_id, upload_id)
