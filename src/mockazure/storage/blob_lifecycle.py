"""Typed Blob metadata recovery; content refs persist, credentials and leases do not."""

import copy
import re
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.content.models import ContentObject
from mockazure.content.repository import object_row, pin_content
from mockazure.identity.types import IdentityInput
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.blob import block_identifier, name_hash
from mockazure.storage.blob_models import Blob, BlobBlock, BlobCursor, BlobVersion


class ContentInput(IdentityInput):
    content_id: UUID
    size: int = Field(ge=0, le=268435456, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=255)


class CommittedBlockInput(IdentityInput):
    id: str
    size: int = Field(ge=0, strict=True)
    offset: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def valid(self):
        try:
            block_identifier(self.id)
        except StorageError:
            raise ValueError("Invalid block identifier") from None
        return self


class VersionInput(ContentInput):
    id: UUID
    stamp: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z$")
    kind: Literal["version", "snapshot"]
    etag: str = Field(pattern=r'^"[A-Za-z0-9]+"$')
    created_at: AwareDatetime
    deleted_at: AwareDatetime | None = None
    deleted_until: AwareDatetime | None = None
    properties: dict = Field(default_factory=dict)
    blocks: list[CommittedBlockInput] = Field(default_factory=list, max_length=50000)

    @model_validator(mode="after")
    def valid(self):
        datetime.fromisoformat(self.stamp.replace("Z", "+00:00"))
        if (self.deleted_at is None) != (self.deleted_until is None) or (
            self.deleted_at is not None and self.deleted_until < self.deleted_at
        ):
            raise ValueError("Invalid deletion retention interval")
        if set(self.properties) - {
            "metadata",
            "content_type",
            "content_md5",
            "content_encoding",
            "content_language",
            "content_disposition",
            "cache_control",
        }:
            raise ValueError("Unsupported Blob property")
        for key, value in self.properties.items():
            if key != "metadata" and (
                not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("Invalid Blob property")
        metadata = self.properties.get("metadata", {})
        if (
            not isinstance(metadata, dict)
            or any(
                not isinstance(k, str)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) is None
                or not isinstance(v, str)
                or not v.isascii()
                or any(ord(c) < 32 for c in v)
                for k, v in metadata.items()
            )
            or sum(len(k) + len(v) + 10 for k, v in metadata.items()) > 8192
        ):
            raise ValueError("Invalid Blob metadata")
        offset = 0
        for block in self.blocks:
            if block.offset != offset:
                raise ValueError("Noncontiguous committed blocks")
            offset += block.size
        if self.blocks and offset != self.size:
            raise ValueError("Committed block sizes do not match content")
        return self


class BlockInput(ContentInput):
    id: UUID
    block_id: str
    created_at: AwareDatetime

    @model_validator(mode="after")
    def valid(self):
        try:
            block_identifier(self.block_id)
        except StorageError:
            raise ValueError("Invalid block identifier") from None
        return self


class BlobInput(IdentityInput):
    id: UUID
    container: str = Field(min_length=3, max_length=63)
    name: str = Field(min_length=1, max_length=1024)
    current_id: UUID | None = None
    changed_at: AwareDatetime
    versions: list[VersionInput] = Field(default_factory=list, max_length=100000)
    blocks: list[BlockInput] = Field(default_factory=list, max_length=100000)

    @model_validator(mode="after")
    def valid(self):
        try:
            name_hash(self.name)
        except StorageError:
            raise ValueError("Invalid Blob name") from None
        if (
            len({v.id for v in self.versions}) != len(self.versions)
            or len({(v.stamp, v.kind) for v in self.versions}) != len(self.versions)
            or len({b.block_id for b in self.blocks}) != len(self.blocks)
            or len({b.id for b in self.blocks}) != len(self.blocks)
        ):
            raise ValueError("Duplicate Blob child")
        if self.current_id is not None and not any(
            v.id == self.current_id and v.kind == "version" and v.deleted_at is None
            for v in self.versions
        ):
            raise ValueError("Current Blob version is invalid")
        return self


async def export_blobs(session, tenant, account):
    blobs = list(
        await session.scalars(
            select(Blob)
            .where(Blob.tenant_id == tenant, Blob.account_id == account)
            .order_by(Blob.name.collate("C"))
            .limit(100001)
        )
    )
    if len(blobs) > 100000:
        raise AdminError(413, "StorageStateLimit", "Blob export exceeds the local metadata limit")
    result = []
    for blob in blobs:
        versions = []
        blocks = []
        for model, target in [(BlobVersion, versions), (BlobBlock, blocks)]:
            rows = (
                await session.execute(
                    select(model, ContentObject)
                    .join(
                        ContentObject,
                        (ContentObject.tenant_id == model.tenant_id)
                        & (ContentObject.id == model.content_id),
                    )
                    .where(model.tenant_id == tenant, model.blob_id == blob.id)
                    .order_by(model.id)
                    .limit(100001)
                )
            ).all()
            if len(rows) > 100000:
                raise AdminError(413, "StorageStateLimit", "Blob child export exceeds local limits")
            for row, content in rows:
                value = {
                    "id": row.id,
                    "content_id": row.content_id,
                    "size": row.size,
                    "sha256": content.sha256,
                    "media_type": content.media_type,
                    "created_at": row.created_at,
                }
                value.update(
                    {
                        "stamp": row.stamp,
                        "kind": row.kind,
                        "etag": row.etag,
                        "deleted_at": row.deleted_at,
                        "deleted_until": row.deleted_until,
                        "properties": row.properties,
                        "blocks": row.blocks,
                    }
                    if model is BlobVersion
                    else {"block_id": row.block_id}
                )
                target.append(value)
        result.append(
            BlobInput(
                id=blob.id,
                container=blob.container,
                name=blob.name,
                current_id=blob.current_id,
                changed_at=blob.changed_at,
                versions=versions,
                blocks=blocks,
            )
        )
    return result


async def validate_blobs(session, tenant, blobs):
    try:
        for blob in blobs:
            for item in [*blob.versions, *blob.blocks]:
                row = await object_row(
                    session, tenant, item.content_id, lock="read", finalized=True
                )
                if (row.size, row.sha256, row.media_type) != (
                    item.size,
                    item.sha256,
                    item.media_type,
                ):
                    raise ValueError("Content manifest mismatch")
    except (AdminError, ValueError):
        raise AdminError(
            422, "InvalidStorageContent", "Blob content is missing or mismatched in this tenant"
        ) from None


async def restore_blobs(session, tenant, account, blobs):
    for item in blobs:
        session.add(
            Blob(
                tenant_id=tenant,
                id=item.id,
                account_id=account,
                container=item.container,
                name=item.name,
                name_hash=name_hash(item.name),
                current_id=item.current_id,
                changed_at=item.changed_at,
            )
        )
        await session.flush()
        for model, entries, kind in [
            (BlobVersion, item.versions, "blob-version"),
            (BlobBlock, item.blocks, "blob-block"),
        ]:
            for entry in entries:
                values = entry.model_dump(exclude={"sha256", "media_type"})
                session.add(model(tenant_id=tenant, blob_id=item.id, **copy.deepcopy(values)))
                await session.flush()
                await pin_content(session, tenant, entry.content_id, kind, str(entry.id))


async def prune_blobs(session, tenant, now, limit):
    count = 0
    for model, criterion in [
        (BlobCursor, BlobCursor.expires_at <= now),
        (BlobBlock, BlobBlock.created_at <= now - timedelta(days=7)),
        (BlobVersion, BlobVersion.deleted_until <= now),
    ]:
        if count >= limit:
            break
        ids = list(
            await session.scalars(
                select(model.id)
                .where(model.tenant_id == tenant, criterion)
                .order_by(model.id)
                .limit(limit - count)
                .with_for_update(skip_locked=True)
            )
        )
        if ids:
            await session.execute(delete(model).where(model.tenant_id == tenant, model.id.in_(ids)))
            count += len(ids)
    return count
