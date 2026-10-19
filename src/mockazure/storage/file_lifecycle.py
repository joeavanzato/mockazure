"""Typed FileREST metadata recovery and bounded ephemeral-state cleanup."""

import copy
from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.content.repository import object_row, pin_content
from mockazure.identity.types import IdentityInput
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.blob_lifecycle import ContentInput
from mockazure.storage.file_models import FileCursor, FileNode, FileTree
from mockazure.storage.files import digest, normalized_path


class FileNodeInput(IdentityInput):
    id: UUID
    path: str = Field(max_length=2048)
    directory: bool = Field(strict=True)
    public_id: UUID
    version_id: UUID
    content: ContentInput | None = None
    size: int = Field(ge=0, le=268435456, strict=True)
    ranges: list[list[int]] = Field(default_factory=list, max_length=100000)
    properties: dict = Field(default_factory=dict)
    created_at: AwareDatetime
    changed_at: AwareDatetime
    etag: str = Field(pattern=r'^"[A-Za-z0-9]+"$')

    @model_validator(mode="after")
    def valid(self):
        try:
            normalized = normalized_path(self.path, directory=self.directory, trailing=True)
        except StorageError:
            raise ValueError("Invalid file path") from None
        if normalized != self.path:
            raise ValueError("Noncanonical file path")
        if self.directory and (self.content is not None or self.size or self.ranges):
            raise ValueError("Directory cannot have content")
        if not self.directory and (self.content is None or self.content.size != self.size):
            raise ValueError("File content size mismatch")
        last = -1
        for pair in self.ranges:
            if (
                len(pair) != 2
                or any(type(v) is not int for v in pair)
                or not 0 <= pair[0] < pair[1] <= self.size
                or pair[0] <= last
            ):
                raise ValueError("Invalid allocated ranges")
            last = pair[1]
        allowed = {
            "metadata",
            "content_type",
            "content_md5",
            "content_encoding",
            "content_language",
            "content_disposition",
            "cache_control",
            "attributes",
            "creation_time",
            "last_write_time",
            "change_time",
        }
        if set(self.properties) - allowed:
            raise ValueError("Unsupported file properties")
        for key, value in self.properties.items():
            if key == "metadata":
                import re

                if (
                    not isinstance(value, dict)
                    or any(
                        not isinstance(k, str)
                        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k)
                        or not isinstance(v, str)
                        or not v.isascii()
                        or any(ord(c) < 32 for c in v)
                        for k, v in value.items()
                    )
                    or sum(len(k) + len(v) + 10 for k, v in value.items()) > 8192
                ):
                    raise ValueError("Invalid file metadata")
            elif (
                not isinstance(value, str)
                or len(value) > 8192
                or not value.isascii()
                or any(ord(c) < 32 for c in value)
            ):
                raise ValueError("Invalid file property")
            if (
                key in {"creation_time", "last_write_time", "change_time"}
                and datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None
            ):
                raise ValueError("Timestamp must be UTC-aware")
        return self


class FileTreeInput(IdentityInput):
    id: UUID
    share: str = Field(min_length=3, max_length=63)
    stamp: str = Field(pattern=r"^(|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z)$")
    created_at: AwareDatetime
    properties: dict = Field(default_factory=dict)
    nodes: list[FileNodeInput] = Field(max_length=100000)

    @model_validator(mode="after")
    def valid(self):
        if self.stamp:
            datetime.fromisoformat(self.stamp.replace("Z", "+00:00"))
        paths = {node.path.casefold(): node for node in self.nodes}
        if (
            len(paths) != len(self.nodes)
            or len({n.id for n in self.nodes}) != len(self.nodes)
            or len({n.public_id for n in self.nodes}) != len(self.nodes)
        ):
            raise ValueError("Duplicate File node")
        if "" not in paths or not paths[""].directory:
            raise ValueError("File tree requires a directory root")
        for node in self.nodes:
            if node.path and (
                node.path.casefold().rpartition("/")[0] not in paths
                or not paths[node.path.casefold().rpartition("/")[0]].directory
            ):
                raise ValueError("Missing directory parent")
        if set(self.properties) - {"metadata", "quota"}:
            raise ValueError("Unsupported snapshot properties")
        # Reuse typed node metadata validation rather than accepting opaque headers.
        FileNodeInput(
            id=paths[""].id,
            path="",
            directory=True,
            public_id=paths[""].public_id,
            version_id=paths[""].version_id,
            size=0,
            properties={"metadata": self.properties.get("metadata", {})},
            created_at=self.created_at,
            changed_at=self.created_at,
            etag='"snapshot"',
        )
        quota = self.properties.get("quota", 5120)
        if type(quota) is not int or not 1 <= quota <= 102400:
            raise ValueError("Invalid snapshot quota")
        return self


async def export_files(session, tenant, account):
    trees = list(
        await session.scalars(
            select(FileTree)
            .where(FileTree.tenant_id == tenant, FileTree.account_id == account)
            .order_by(FileTree.share, FileTree.stamp)
            .limit(10001)
        )
    )
    if len(trees) > 10000:
        raise AdminError(413, "StorageStateLimit", "File tree export limit exceeded")
    result = []
    for tree in trees:
        nodes = list(
            await session.scalars(
                select(FileNode)
                .where(FileNode.tenant_id == tenant, FileNode.tree_id == tree.id)
                .order_by(FileNode.path_key)
                .limit(100001)
            )
        )
        if len(nodes) > 100000:
            raise AdminError(413, "StorageStateLimit", "File node export limit exceeded")
        values = []
        for node in nodes:
            content = (
                await object_row(session, tenant, node.content_id, finalized=True)
                if node.content_id
                else None
            )
            values.append(
                FileNodeInput(
                    id=node.id,
                    path=node.path,
                    directory=node.directory,
                    public_id=node.public_id,
                    version_id=node.version_id,
                    content=ContentInput(
                        content_id=content.id,
                        size=content.size,
                        sha256=content.sha256,
                        media_type=content.media_type,
                    )
                    if content
                    else None,
                    size=node.size,
                    ranges=node.ranges,
                    properties=node.properties,
                    created_at=node.created_at,
                    changed_at=node.changed_at,
                    etag=node.etag,
                )
            )
        result.append(
            FileTreeInput(
                id=tree.id,
                share=tree.share,
                stamp=tree.stamp,
                created_at=tree.created_at,
                properties=tree.properties,
                nodes=values,
            )
        )
    return result


async def validate_files(session, tenant, trees):
    for tree in trees:
        for node in tree.nodes:
            if node.content:
                content = await object_row(session, tenant, node.content.content_id, finalized=True)
                if any(
                    getattr(content, k) != getattr(node.content, k)
                    for k in ("size", "sha256", "media_type")
                ):
                    raise AdminError(
                        422, "InvalidStorageState", "File bytes do not match the manifest"
                    )


async def restore_files(session, tenant, account, trees):
    for item in trees:
        tree = FileTree(
            tenant_id=tenant,
            id=item.id,
            account_id=account,
            share=item.share,
            stamp=item.stamp,
            created_at=item.created_at,
            properties=copy.deepcopy(item.properties),
        )
        session.add(tree)
        await session.flush()
        for value in item.nodes:
            node = FileNode(
                tenant_id=tenant,
                tree_id=tree.id,
                id=value.id,
                path=value.path,
                path_key=value.path.casefold(),
                path_hash=digest(value.path),
                parent_hash=digest(value.path.rpartition("/")[0]),
                directory=value.directory,
                public_id=value.public_id,
                version_id=value.version_id,
                content_id=value.content.content_id if value.content else None,
                size=value.size,
                ranges=copy.deepcopy(value.ranges),
                properties=copy.deepcopy(value.properties),
                created_at=value.created_at,
                changed_at=value.changed_at,
                etag=value.etag,
                lease_state="available",
            )
            session.add(node)
            await session.flush()
            if node.content_id:
                await pin_content(session, tenant, node.content_id, "file-node", str(node.id))


async def prune_files(session, tenant, now, limit):
    ids = list(
        await session.scalars(
            select(FileCursor.id)
            .where(FileCursor.tenant_id == tenant, FileCursor.expires_at <= now)
            .order_by(FileCursor.expires_at, FileCursor.id)
            .limit(limit)
        )
    )
    if ids:
        await session.execute(
            delete(FileCursor).where(FileCursor.tenant_id == tenant, FileCursor.id.in_(ids))
        )
    return len(ids)
