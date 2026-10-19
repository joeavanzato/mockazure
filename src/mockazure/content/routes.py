"""Private content management; public workload routes authorize their own resources."""

from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from mockazure.admin.errors import AdminError
from mockazure.admin.mail_routes import parsed
from mockazure.content.models import CHUNK_SIZE
from mockazure.content.ranges import content_range
from mockazure.content.repository import (
    begin_upload,
    finalize_upload,
    object_row,
    put_chunk,
    reset_content,
    stream_content,
)
from mockazure.jobs.exports import export_content_manifest, validate_content_restore

router = APIRouter(prefix="/_mock/tenants/{tenant_id}/content")


class UploadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    size: int = Field(ge=0, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    media_type: str = Field(min_length=3, max_length=255)


class ManifestItem(UploadInput):
    id: UUID


class ManifestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    content: list[ManifestItem] = Field(max_length=10000)
    mapping: dict[UUID, UUID] = Field(default_factory=dict, max_length=10000)


@router.post("/uploads", status_code=201)
async def create_upload(tenant_id: UUID, request: Request):
    value = await parsed(request, UploadInput)
    async with request.app.state.database.sessions.begin() as session:
        identifier = await begin_upload(
            session,
            tenant_id,
            value.size,
            value.sha256,
            value.media_type,
            max_size=request.app.state.settings.content_max_object_bytes,
        )
        return {"uploadId": str(identifier), "chunkSize": CHUNK_SIZE}


@router.put("/uploads/{upload_id}/chunks/{ordinal}", status_code=204)
async def upload_chunk(tenant_id: UUID, upload_id: UUID, ordinal: int, request: Request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > CHUNK_SIZE:
            raise AdminError(413, "ChunkTooLarge", "Content chunks cannot exceed one MiB")
        data.extend(chunk)
    async with request.app.state.database.sessions.begin() as session:
        await put_chunk(session, tenant_id, upload_id, ordinal, bytes(data))
    return Response(status_code=204)


@router.post("/uploads/{upload_id}/complete")
async def complete_upload(tenant_id: UUID, upload_id: UUID, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        return asdict(await finalize_upload(session, tenant_id, upload_id))


@router.get("/objects/{content_id}/$value")
async def download(tenant_id: UUID, content_id: UUID, request: Request):
    session = request.app.state.database.sessions()
    try:
        row = await object_row(session, tenant_id, content_id, lock="read", finalized=True)
        start, end = content_range(request.headers.get("range"), row.size)
        headers = {
            "Content-Length": str(end - start),
            "Accept-Ranges": "bytes",
            "Content-Disposition": f'attachment; filename="{content_id}.bin"',
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
            "ETag": '"' + row.sha256 + '"',
        }
        status = 200
        if request.headers.get("range") is not None:
            status = 206
            headers["Content-Range"] = f"bytes {start}-{end - 1}/{row.size}"

        async def body():
            try:
                async for part in stream_content(session, tenant_id, content_id, start, end):
                    yield part
            finally:
                await session.close()

        return StreamingResponse(
            body(),
            status_code=status,
            headers=headers,
            media_type=row.media_type,
            background=BackgroundTask(session.close),
        )
    except BaseException:
        await session.close()
        raise


@router.get("/manifest")
async def manifest(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions() as session:
        return await export_content_manifest(session, tenant_id)


@router.post("/validate")
async def validate(tenant_id: UUID, request: Request):
    value = await parsed(request, ManifestInput)
    async with request.app.state.database.sessions() as session:
        data = value.model_dump(mode="json")
        return await validate_content_restore(session, tenant_id, data["content"], data["mapping"])


@router.post("/reset")
async def reset(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        return await reset_content(session, tenant_id)
