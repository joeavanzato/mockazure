"""Bounded synthetic byte generation through the normal chunked content repository."""

import hashlib

from pydantic import Field
from sqlalchemy import select

from mockazure.content.models import CHUNK_SIZE, ContentObject
from mockazure.content.repository import begin_upload, finalize_upload, put_chunk
from mockazure.population.adapters.browsing import browser
from mockazure.population.corpus.models import ContentSpec
from mockazure.population.corpus.render import render
from mockazure.population.types import (
    BatchResult,
    Input,
    Inspection,
    OperationDescriptor,
    ResourceQuery,
)


class GeneratedContent(Input):
    text: str = Field(min_length=1, max_length=65536)
    repeat: int = Field(default=1, ge=1, le=1000000, strict=True)
    media_type: str = "text/plain"

    def chunks(self):
        pattern = self.text.encode("utf-8")
        size = len(pattern) * self.repeat
        if size > 16 * 1024 * 1024:
            raise ValueError("Generated profile content exceeds 16 MiB; use explicit uploads")
        block = pattern * (CHUNK_SIZE // len(pattern) + 2)
        for start in range(0, size, CHUNK_SIZE):
            offset = start % len(pattern)
            yield block[offset : offset + min(CHUNK_SIZE, size - start)]

    def metadata(self):
        digest, size = hashlib.sha256(), 0
        for chunk in self.chunks():
            digest.update(chunk)
            size += len(chunk)
        return size, digest.hexdigest()


class ContentAdapter:
    def __init__(self):
        r = ContentObject
        self.base = browser(
            "content.objects",
            "Content",
            "/_mock/tenants/{tenant}/content/objects/{id}/$value",
            r,
            r.id,
            r.sha256,
            {"id": r.id, "size": r.size, "sha256": r.sha256, "media_type": r.media_type},
            conditions=(r.state == "finalized",),
        )

    def describe(self):
        descriptor = self.base.describe().model_copy(deep=True)
        descriptor.operations = [
            OperationDescriptor(
                key="generate",
                mode="fixture",
                method="POST",
                path_template="/_mock/tenants/{tenant}/content/uploads",
                api_version="local-fixture-v1",
                input_schema=GeneratedContent.model_json_schema(),
            ),
            OperationDescriptor(
                key="render",
                mode="fixture",
                method="POST",
                path_template="/_mock/tenants/{tenant}/content/uploads",
                api_version="local-fixture-v1",
                input_schema=ContentSpec.model_json_schema(),
            ),
        ]
        descriptor.limitations = [
            "Generate up to 16 MiB from explicit text; larger/binary files use chunked upload "
            "routes. No server paths or URL fetches."
            " Render synthetic TXT/CSV/JSON/PDF/DOCX/XLSX/PPTX documents up to 8 MiB."
        ]
        return descriptor

    def validate(self, mutation):
        self.value(mutation).metadata()

    def value(self, mutation):
        if mutation.operation == "render":
            return render(ContentSpec.model_validate(mutation.payload))
        if mutation.operation != "generate":
            raise ValueError("Unsupported content operation")
        return GeneratedContent.model_validate(mutation.payload)

    async def browse(self, ctx, query):
        return await self.base.browse(ctx, query)

    async def existing(self, ctx, value):
        size, digest = value.metadata()
        async with ctx.database.sessions() as session:
            return await session.scalar(
                select(ContentObject.id)
                .where(
                    ContentObject.tenant_id == ctx.tenant_id,
                    ContentObject.state == "finalized",
                    ContentObject.sha256 == digest,
                    ContentObject.size == size,
                    ContentObject.media_type == value.media_type,
                )
                .limit(1)
            )

    async def inspect(self, ctx, mutation):
        value = self.value(mutation)
        identifier = await self.existing(ctx, value)
        if identifier is None:
            return Inspection(actual_id=None, version=None, exists=False)
        item = (await self.browse(ctx, ResourceQuery(identifier=str(identifier)))).items[0]
        return Inspection(actual_id=item.id, version=item.version, exists=True)

    async def apply(self, ctx, mutations):
        if len(mutations) != 1:
            raise ValueError("Generate one content object per checkpoint")
        mutation = mutations[0]
        value = self.value(mutation)
        old = await self.existing(ctx, value)
        identifier = old
        if old is None:
            size, digest = value.metadata()
            async with ctx.database.sessions.begin() as session:
                upload = await begin_upload(
                    session,
                    ctx.tenant_id,
                    size,
                    digest,
                    value.media_type,
                    max_size=ctx.settings.content_max_object_bytes,
                )
                for ordinal, chunk in enumerate(value.chunks()):
                    await put_chunk(session, ctx.tenant_id, upload, ordinal, chunk)
                finalized = await finalize_upload(session, ctx.tenant_id, upload)
                identifier = finalized.id
        page = await self.browse(ctx, ResourceQuery(identifier=str(identifier)))
        return BatchResult(
            items=page.items,
            effects={"created": int(old is None), "updated": 0, "unchanged": int(old is not None)},
        )

    async def verify(self, ctx, result):
        return await self.base.verify(ctx, result)


def adapters():
    return [ContentAdapter()]
