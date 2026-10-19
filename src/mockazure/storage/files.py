"""FileREST names, immutable byte revisions, sparse ranges and share snapshots."""

import copy
import hashlib
import unicodedata
from datetime import UTC, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select

from mockazure.cloud.preconditions import check_local_write
from mockazure.content.models import CHUNK_SIZE, DEFAULT_MAX_SIZE
from mockazure.content.repository import pin_content, release_content, stream_content
from mockazure.content.stream_upload import (
    append_stream_chunk,
    begin_stream_upload,
    complete_stream_upload,
)
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.containers import record
from mockazure.storage.file_models import FileNode, FileTree
from mockazure.storage.models import StorageNamespace
from mockazure.storage.types import ContentVersion


def digest(value):
    return hashlib.sha256(value.casefold().encode()).hexdigest()


def normalized_path(value, *, directory=False, trailing=False):
    if directory:
        value = value.rstrip("/")
    if not value:
        return ""
    parts = value.split("/")
    if not trailing:
        parts = [v.rstrip(".") for v in parts]
    forbidden = set('"\\:|<>*?')
    reserved = {
        ".",
        "..",
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        *[f"LPT{i}" for i in range(1, 10)],
        *[f"COM{i}" for i in range(1, 10)],
    }
    if (
        len(parts) > 251
        or len("/".join(parts)) > 2048
        or any(
            not p
            or len(p) > 255
            or p.upper() in reserved
            or any(c in forbidden or unicodedata.category(c) in {"Cc", "Cs", "Co"} for c in p)
            for p in parts
        )
    ):
        raise StorageError(400, "InvalidResourceName", "Invalid FileREST path")
    return "/".join(parts)


async def tree_for(session, access, share, now, stamp="", *, create=False):
    namespace = await session.get(
        StorageNamespace, (access.tenant_id, access.account_id, "file", share)
    )
    if namespace is None:
        raise StorageError(404, "ShareNotFound", "Share was not found")
    tree = await session.scalar(
        select(FileTree).where(
            FileTree.tenant_id == access.tenant_id,
            FileTree.account_id == access.account_id,
            FileTree.share == share,
            FileTree.stamp == stamp,
        )
    )
    if tree is None and create and not stamp:
        tree = FileTree(
            tenant_id=access.tenant_id,
            account_id=access.account_id,
            share=share,
            stamp="",
            created_at=now,
            properties=copy.deepcopy(namespace.properties),
        )
        session.add(tree)
        await session.flush()
        root = FileNode(
            tenant_id=access.tenant_id,
            tree_id=tree.id,
            path="",
            path_key="",
            path_hash=digest(""),
            parent_hash=digest(""),
            directory=True,
            properties={},
            created_at=now,
            changed_at=now,
            etag='"' + uuid4().hex + '"',
        )
        session.add(root)
        await session.flush()
    if tree is None:
        raise StorageError(
            404,
            "ShareSnapshotNotFound" if stamp else "ResourceNotFound",
            "Share view was not found",
        )
    return tree


async def node_for(session, tree, path, *, required=True):
    row = await session.scalar(
        select(FileNode)
        .where(
            FileNode.tenant_id == tree.tenant_id,
            FileNode.tree_id == tree.id,
            FileNode.path_hash == digest(path),
        )
        .with_for_update()
    )
    if row is not None and row.path_key != path.casefold():
        raise StorageError(409, "ResourceAlreadyExists", "Path hash conflict")
    if row is None and required:
        raise StorageError(404, "ResourceNotFound", "File or directory was not found")
    return row


def check_lease(row, supplied, *, write=False):
    if supplied:
        try:
            supplied = str(UUID(supplied))
        except ValueError:
            raise StorageError(400, "InvalidHeaderValue", "Invalid file lease ID") from None
    active = row is not None and row.lease_state == "leased"
    if active and (write or supplied) and supplied != str(row.lease_id):
        raise StorageError(
            412,
            "LeaseIdMissing" if not supplied else "LeaseIdMismatchWithFileOperation",
            "File lease condition failed",
        )
    if not active and supplied:
        raise StorageError(
            412, "LeaseNotPresentWithFileOperation", "File lease is no longer active"
        )


def changed(row, now):
    row.version_id = uuid4()
    row.etag = '"' + row.version_id.hex + '"'
    row.changed_at = now
    if row.lease_state == "broken":
        row.lease_state = "available"
        row.lease_id = None


def checked_size(value):
    if isinstance(value, bool):
        raise ValueError("Invalid size")
    size = int(value)
    if not 0 <= size <= DEFAULT_MAX_SIZE:
        raise StorageError(413, "RequestBodyTooLarge", "Local file size limit is 256 MiB")
    return size


async def make_content(session, tenant, old, new_size, media_type, start=0, data=None, length=0):
    upload = await begin_stream_upload(session, tenant, new_size, DEFAULT_MAX_SIZE)
    old_size = old.size if old else 0
    # Read/copy in bounded immutable chunks; never materialize a whole file.
    for offset in range(0, new_size, CHUNK_SIZE):
        end = min(new_size, offset + CHUNK_SIZE)
        chunk = bytearray(end - offset)
        if old and offset < old_size:
            cursor = 0
            async for part in stream_content(
                session, tenant, old.content_id, offset, min(end, old_size)
            ):
                chunk[cursor : cursor + len(part)] = part
                cursor += len(part)
        low = max(offset, start)
        high = min(end, start + length)
        if low < high:
            chunk[low - offset : high - offset] = (
                data[low - start : high - start] if data is not None else bytes(high - low)
            )
        await append_stream_chunk(session, tenant, upload, bytes(chunk))
    return await complete_stream_upload(session, tenant, upload, media_type)


async def publish_content(session, row, ref, now):
    await release_content(session, row.tenant_id, "file-node", str(row.id))
    row.content_id = ref.id
    row.size = ref.size
    changed(row, now)
    await session.flush()
    await pin_content(session, row.tenant_id, ref.id, "file-node", str(row.id))


def update_ranges(ranges, start, end, clear=False):
    output = []
    for low, high in ranges:
        if high <= start or low >= end:
            output.append([low, high])
            continue
        if low < start:
            output.append([low, start])
        if high > end:
            output.append([end, high])
    if not clear:
        output.append([start, end])
    merged = []
    for low, high in sorted(output):
        if low >= high:
            continue
        if merged and low <= merged[-1][1]:
            merged[-1][1] = max(high, merged[-1][1])
        else:
            merged.append([low, high])
    return merged


async def create_node(session, access, share, path, directory, size, properties, conditions, now):
    tree = await tree_for(session, access, share, now, create=True)
    if not path:
        raise StorageError(409, "ResourceAlreadyExists", "The share root already exists")
    parent = await node_for(session, tree, path.rpartition("/")[0])
    if not parent.directory:
        raise StorageError(404, "ParentNotFound", "Parent directory was not found")
    row = await node_for(session, tree, path, required=False)
    check_local_write(conditions, row, StorageError)
    if row is not None and (row.directory or directory):
        raise StorageError(409, "ResourceAlreadyExists", "File or directory already exists")
    check_lease(row, conditions.get("x-ms-lease-id"), write=True)
    if row is None:
        row = FileNode(
            tenant_id=access.tenant_id,
            id=uuid4(),
            tree_id=tree.id,
            path=path,
            path_key=path.casefold(),
            path_hash=digest(path),
            parent_hash=digest(path.rpartition("/")[0]),
            directory=directory,
            properties=properties,
            created_at=now,
            changed_at=now,
            etag='"' + uuid4().hex + '"',
        )
        if not directory:
            ref = await make_content(
                session,
                access.tenant_id,
                None,
                checked_size(size),
                properties.get("content_type", "application/octet-stream"),
            )
            row.content_id = ref.id
            row.size = ref.size
        session.add(row)
        await session.flush()
        if not directory:
            await pin_content(session, row.tenant_id, row.content_id, "file-node", str(row.id))
    else:
        ref = await make_content(
            session,
            access.tenant_id,
            None,
            checked_size(size),
            properties.get("content_type", "application/octet-stream"),
        )
        await publish_content(session, row, ref, now)
        row.properties = properties
        row.ranges = []
    await record(
        session,
        access,
        "file",
        share + "/" + path,
        "CreateDirectory" if directory else "CreateFile",
        now,
    )
    return row


async def write_file_range(
    session, access, share, path, start, data, length, conditions, now
) -> ContentVersion:
    tree = await tree_for(session, access, share, now)
    row = await node_for(session, tree, path)
    if row.directory:
        raise StorageError(404, "ResourceNotFound", "File was not found")
    check_lease(row, conditions.get("x-ms-lease-id"), write=True)
    check_local_write(conditions, row, StorageError)
    if (
        type(start) is not int
        or type(length) is not int
        or not 0 <= start < start + length <= row.size
    ):
        raise StorageError(416, "InvalidRange", "Range is outside the file")
    if data is not None and (len(data) != length or length > 4194304):
        raise StorageError(400, "InvalidRange", "Update body must match a range of at most 4 MiB")
    ref = await make_content(
        session,
        access.tenant_id,
        row,
        row.size,
        row.properties.get("content_type", "application/octet-stream"),
        start,
        data,
        length,
    )
    if data is None:
        # FileREST clears every requested byte, but deallocates only complete sectors.
        first_sector = ((start + 511) // 512) * 512
        last_sector = ((start + length) // 512) * 512
        if first_sector < last_sector:
            row.ranges = update_ranges(row.ranges, first_sector, last_sector, True)
    else:
        row.ranges = update_ranges(row.ranges, start, start + length)
    await publish_content(session, row, ref, now)
    await record(session, access, "file", share + "/" + path, "PutRange", now)
    return ContentVersion(row.version_id, ref.id, row.etag, row.size)


async def snapshot_share(session, access, share, metadata, now):
    tree = await tree_for(session, access, share, now, create=True)
    count = await session.scalar(
        select(func.count())
        .select_from(FileTree)
        .where(
            FileTree.tenant_id == access.tenant_id,
            FileTree.account_id == access.account_id,
            FileTree.share == share,
        )
    )
    if count > 200:
        raise StorageError(409, "ShareSnapshotCountExceeded", "Share snapshot limit is 200")
    when = now.astimezone(UTC)
    while await session.scalar(
        select(FileTree.id).where(
            FileTree.tenant_id == access.tenant_id,
            FileTree.account_id == access.account_id,
            FileTree.share == share,
            FileTree.stamp == when.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z",
        )
    ):
        when += timedelta(microseconds=1)
    namespace = await session.get(
        StorageNamespace, (access.tenant_id, access.account_id, "file", share)
    )
    props = copy.deepcopy(namespace.properties)
    if metadata is not None:
        props["metadata"] = metadata
    snapshot = FileTree(
        tenant_id=access.tenant_id,
        account_id=access.account_id,
        share=share,
        stamp=when.strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z",
        created_at=now,
        properties=props,
    )
    session.add(snapshot)
    await session.flush()
    rows = await session.stream_scalars(
        select(FileNode)
        .where(FileNode.tenant_id == access.tenant_id, FileNode.tree_id == tree.id)
        .execution_options(yield_per=100)
    )
    async for old in rows:
        values = {
            c.name: copy.deepcopy(getattr(old, c.name))
            for c in FileNode.__table__.columns
            if c.name not in {"id", "tree_id", "lease_id", "lease_state"}
        }
        node = FileNode(**values, id=uuid4(), tree_id=snapshot.id, lease_state="available")
        session.add(node)
        await session.flush()
        if node.content_id:
            await pin_content(session, access.tenant_id, node.content_id, "file-node", str(node.id))
    await record(session, access, "file", share, "SnapshotShare", now)
    return snapshot
