"""Blob REST adapter; payloads stay in bounded immutable PostgreSQL chunks."""

import base64
import copy
import hashlib
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from xml.etree import ElementTree as ET

from sqlalchemy import delete, select
from starlette.responses import Response, StreamingResponse

from mockazure.admin.errors import AdminError
from mockazure.content.models import DEFAULT_MAX_SIZE
from mockazure.content.repository import object_row, reference, stream_content
from mockazure.content.stream_upload import append_stream_chunk, begin_stream_upload
from mockazure.identity.keys import read_wrapping_key
from mockazure.providers.storage.errors import StorageError, headers
from mockazure.storage.authorization import authorize_storage
from mockazure.storage.blob import (
    check_conditions,
    commit_blocks,
    find_blob,
    new_version,
    profile,
    put_blob,
    selected_version,
    stage_block,
)
from mockazure.storage.blob_models import BlobBlock, BlobLease, BlobVersion
from mockazure.storage.containers import record
from mockazure.storage.leases import lease_headers, operate_lease, require_lease
from mockazure.storage.lifecycle import lock_accounts
from mockazure.storage.models import StorageAccount


class TransactionalStream(StreamingResponse):
    def __init__(self, content, session, **kwargs):
        super().__init__(content, **kwargs)
        self.session = session

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
            await self.session.commit()
        except BaseException:
            await self.session.rollback()
            raise
        finally:
            await self.session.close()


async def detached_reader(request, session, tenant, content_id):
    """Transfer immutable bytes to a content-only read lock before releasing authority locks."""
    reader = request.app.state.database.sessions()
    try:
        await object_row(reader, tenant, content_id, lock="read", finalized=True)
        await session.commit()
        await session.close()
        return reader
    except BaseException:
        await reader.close()
        raise


def properties(request, *, inherit=None, body_type=False):
    from mockazure.providers.storage.routes import metadata_headers

    values = copy.deepcopy(inherit) if inherit is not None else {}
    if inherit is None or any(k.startswith("x-ms-meta-") for k in request.headers):
        values["metadata"] = metadata_headers(request)
    for header, key in [
        ("type", "content_type"),
        ("encoding", "content_encoding"),
        ("language", "content_language"),
        ("disposition", "content_disposition"),
        ("md5", "content_md5"),
    ]:
        if body_type and "content-" + header in request.headers:
            values[key] = request.headers["content-" + header]
        if "x-ms-blob-content-" + header in request.headers:
            values[key] = request.headers["x-ms-blob-content-" + header]
    if body_type and "cache-control" in request.headers:
        values["cache_control"] = request.headers["cache-control"]
    if "x-ms-blob-cache-control" in request.headers:
        values["cache_control"] = request.headers["x-ms-blob-cache-control"]
    values.setdefault(
        "content_type",
        request.headers.get("content-type", "application/octet-stream")
        if body_type
        else "application/octet-stream",
    )
    if values.get("content_md5"):
        try:
            if len(base64.b64decode(values["content_md5"], validate=True)) != 16:
                raise ValueError()
        except ValueError:
            raise StorageError(400, "InvalidHeaderValue", "Invalid Blob MD5 property") from None
    return values


async def response_properties(session, request, blob, version, now, settings):
    props = version.properties
    result = headers(request) | {
        "ETag": version.etag,
        "Last-Modified": format_datetime(version.created_at.astimezone(UTC), usegmt=True),
        "Content-Length": str(version.size),
        "Content-Type": props.get("content_type", "application/octet-stream"),
        "x-ms-blob-type": "BlockBlob",
        "x-ms-server-encrypted": "true",
        "Accept-Ranges": "bytes",
    }
    for key, header in [
        ("content_md5", "Content-MD5"),
        ("content_encoding", "Content-Encoding"),
        ("content_language", "Content-Language"),
        ("content_disposition", "Content-Disposition"),
        ("cache_control", "Cache-Control"),
    ]:
        if key in props:
            result[header] = props[key]
    result.update({"x-ms-meta-" + k: v for k, v in props.get("metadata", {}).items()})
    result.update(
        {"x-ms-lease-state": "available", "x-ms-lease-status": "unlocked"}
        if version.kind == "snapshot" or version.id != blob.current_id
        else await lease_headers(session, blob, now)
    )
    if settings.get("isVersioningEnabled") and version.kind == "version":
        result["x-ms-version-id"] = version.stamp
    return result


async def receive_upload(session, request, access):
    raw = request.headers.get("content-length")
    length = int(raw) if raw is not None else None
    upload = await begin_stream_upload(session, access.tenant_id, length, DEFAULT_MAX_SIZE)
    md5 = hashlib.md5(usedforsecurity=False)
    async for chunk in request.stream():
        md5.update(chunk)
        await append_stream_chunk(session, access.tenant_id, upload, chunk)
    digest = base64.b64encode(md5.digest()).decode()
    if request.headers.get("content-md5", digest) != digest:
        raise StorageError(400, "Md5Mismatch", "Request content MD5 does not match")
    return upload, digest


def byte_range(request, size):
    value = request.headers.get("x-ms-range", request.headers.get("range"))
    if value is None:
        return 0, size, 200
    import re

    match = re.fullmatch(r"bytes=(\d+)-(\d*)", value)
    if match is None:
        raise StorageError(416, "InvalidRange", "Unsupported or malformed byte range")
    start = int(match[1])
    end = min(size, int(match[2]) + 1) if match[2] else size
    if not 0 <= start < end <= size:
        raise StorageError(416, "InvalidRange", "Requested range is outside the blob")
    return start, end, 206


async def dispatch_blob(request, account, container, name):
    from mockazure.providers.storage.routes import bounded_body, xml_response

    args = request.query_params
    version = request.headers.get(
        "x-ms-version", args.get("api-version", args.get("sv", "2025-11-05"))
    )
    if version not in {"2019-07-07", "2025-01-05", "2025-11-05"}:
        raise StorageError(
            400, "InvalidHeaderValue", "Blob content profile requires version 2025-11-05"
        )
    comp = args.get("comp")
    method = request.method
    if not name and args.get("restype") == "container" and comp == "list" and method == "GET":
        operation = "list"
    elif name and comp is None and method in {"GET", "HEAD", "PUT", "DELETE"}:
        operation = {"GET": "read", "HEAD": "head", "PUT": "put", "DELETE": "delete"}[method]
    elif name and comp == "metadata" and method in {"GET", "HEAD"}:
        operation = "get_metadata"
    elif name and comp in {
        "metadata",
        "properties",
        "block",
        "blocklist",
        "snapshot",
        "lease",
        "undelete",
    }:
        operation = comp
        if method != "PUT" and not (comp == "blocklist" and method == "GET"):
            raise StorageError(405, "UnsupportedHttpVerb", "Unsupported verb for Blob operation")
    else:
        raise StorageError(
            501, "MockAzureOperationNotImplemented", "Blob operation is not implemented"
        )
    if version == "2025-01-05" and operation not in {"read", "head", "get_metadata"}:
        raise StorageError(400, "InvalidHeaderValue", "Unsupported 2025-01 Blob content operation")
    if version in {"2019-07-07", "2025-01-05"} and (
        operation
        not in {
            "put",
            "read",
            "head",
            "get_metadata",
            "block",
            "blocklist",
            "list",
            "metadata",
            "properties",
            "delete",
        }
        or any(k in args for k in ("versionid", "snapshot"))
        or set(args.get("include", "").split(",")) - {"", "metadata"}
        or not request.headers.get("Authorization", "").startswith(("SharedKey ", "SharedKeyLite "))
    ):
        raise StorageError(400, "InvalidHeaderValue", "Unsupported legacy Blob content profile")
    auth_keys = {
        "sv",
        "ss",
        "srt",
        "sp",
        "st",
        "se",
        "sip",
        "spr",
        "ses",
        "sig",
        "sr",
        "si",
        "skoid",
        "sktid",
        "skt",
        "ske",
        "sks",
        "skv",
        "saoid",
        "suoid",
        "scid",
        "skdutid",
        "sduoid",
        "rscc",
        "rscd",
        "rsce",
        "rscl",
        "rsct",
    }
    allowed = {"comp", "timeout", "api-version"} | auth_keys
    if operation in {"read", "head", "delete", "get_metadata"}:
        allowed |= {"snapshot", "versionid"}
    if operation == "list":
        allowed |= {"restype", "prefix", "delimiter", "marker", "maxresults", "include"}
    if operation == "block":
        allowed |= {"blockid"}
    if operation == "blocklist" and method == "GET":
        allowed |= {"blocklisttype", "snapshot", "versionid"}
    if set(args) - allowed or ("snapshot" in args and "versionid" in args):
        raise StorageError(400, "InvalidQueryParameterValue", "Unsupported Blob query parameter")
    common = {
        "x-ms-date",
        "x-ms-version",
        "x-ms-client-request-id",
        "x-ms-return-client-request-id",
    }
    blob_properties = {
        "x-ms-blob-content-type",
        "x-ms-blob-content-md5",
        "x-ms-blob-content-encoding",
        "x-ms-blob-content-language",
        "x-ms-blob-content-disposition",
        "x-ms-blob-cache-control",
    }
    specific = {
        "list": set(),
        "read": {"x-ms-lease-id", "x-ms-range", "x-ms-range-get-content-md5"},
        "head": {"x-ms-lease-id"},
        "get_metadata": {"x-ms-lease-id"},
        "put": {"x-ms-lease-id", "x-ms-blob-type"} | blob_properties,
        "block": {"x-ms-lease-id"},
        "blocklist": {"x-ms-lease-id"} | (blob_properties if method == "PUT" else set()),
        "metadata": {"x-ms-lease-id"},
        "properties": {"x-ms-lease-id"} | blob_properties,
        "snapshot": {"x-ms-lease-id"},
        "delete": {"x-ms-lease-id", "x-ms-delete-snapshots"},
        "undelete": set(),
        "lease": {"x-ms-lease-action"},
    }[operation]
    if operation == "lease":
        specific |= {
            "acquire": {"x-ms-lease-duration", "x-ms-proposed-lease-id"},
            "renew": {"x-ms-lease-id"},
            "release": {"x-ms-lease-id"},
            "change": {"x-ms-lease-id", "x-ms-proposed-lease-id"},
            "break": {"x-ms-lease-break-period"},
        }.get(request.headers.get("x-ms-lease-action"), set())
    # The original Python SDK carries upload metadata on staged blocks.
    # Put Block does not publish it; only the eventual Put Block List does.
    allow_metadata = operation in {"put", "block", "metadata", "snapshot"} or (
        operation == "blocklist" and method == "PUT"
    )
    for key in request.headers:
        if (
            key.startswith("x-ms-")
            and key not in common | specific
            and not (allow_metadata and key.startswith("x-ms-meta-"))
        ):
            raise StorageError(
                400, "InvalidHeaderValue", "This header is not supported for the Blob operation"
            )
    if operation == "put" and request.headers.get("x-ms-blob-type") != "BlockBlob":
        raise StorageError(400, "InvalidBlobType", "Only BlockBlob uploads are supported")
    action = (
        "list_blobs"
        if operation == "list"
        else "delete_blob"
        if operation == "delete"
        else "read_blob"
        if method in {"GET", "HEAD"}
        else "write_blob"
    )
    now = datetime.now(UTC)
    session = request.app.state.database.sessions()
    await session.begin()
    streaming = False
    try:
        session.info["storage_cipher"] = read_wrapping_key(request.app.state.settings.data_dir)
        owner = await session.scalar(
            select(StorageAccount.tenant_id).where(StorageAccount.name == account)
        )
        if owner is None:
            raise StorageError(404, "ResourceNotFound", "Storage account was not found")
        await lock_accounts(session, owner)
        now = datetime.now(UTC)
        access = await authorize_storage(
            session, request, "blob", action, container + ("/" + name if name else ""), now
        )
        settings = await profile(session, access)
        values = dict(request.headers)
        if (
            access.mode.endswith("_sas")
            and action == "write_blob"
            and "w" not in access.permissions
        ):
            # 2025-11 permits c only for Put Blob creating a new block blob.
            # Put Block/List support for c first appears in 2026-04-06.
            existing = await find_blob(session, access, container, name, now)
            if operation != "put" or await selected_version(session, existing) is not None:
                raise StorageError(
                    403, "AuthorizationPermissionMismatch", "Write permission is required"
                )
        overrides = {}
        for query, header in (
            ("rscc", "Cache-Control"),
            ("rscd", "Content-Disposition"),
            ("rsce", "Content-Encoding"),
            ("rscl", "Content-Language"),
            ("rsct", "Content-Type"),
        ):
            if query in args:
                value = args[query]
                if (
                    access.mode not in {"service_sas", "user_delegation_sas"}
                    or operation not in {"read", "head"}
                    or not value.isascii()
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)
                ):
                    raise StorageError(
                        400, "InvalidQueryParameterValue", "Unsupported signed response override"
                    )
                overrides[header] = value
        if operation == "list":
            from mockazure.storage.blob_paging import list_blobs

            xml = await list_blobs(
                session, access, container, args, now, str(request.url).split("?")[0]
            )
            result = xml_response(request, xml)
        elif operation in {"put", "block"}:
            upload, md5 = await receive_upload(session, request, access)
            now = datetime.now(UTC)
            access = await authorize_storage(
                session, request, "blob", action, container + "/" + name, now
            )
            if operation == "block":
                await stage_block(
                    session, access, container, name, args.get("blockid"), upload, values, now
                )
                result = Response(
                    status_code=201,
                    headers=headers(request)
                    | {"Content-MD5": md5, "x-ms-request-server-encrypted": "true"},
                )
            else:
                props = properties(request, body_type=True)
                props.setdefault("content_md5", md5)
                version = await put_blob(
                    session, access, container, name, upload, values | {"properties": props}, now
                )
                row = await session.get(BlobVersion, (access.tenant_id, version.id))
                result = Response(
                    status_code=201,
                    headers=headers(request)
                    | {
                        "ETag": row.etag,
                        "Last-Modified": format_datetime(now, usegmt=True),
                        "Content-MD5": md5,
                        "x-ms-request-server-encrypted": "true",
                    }
                    | (
                        {"x-ms-version-id": row.stamp}
                        if settings.get("isVersioningEnabled")
                        else {}
                    ),
                )
        elif operation == "blocklist" and method == "PUT":
            raw = await bounded_body(request, 4 * 1024 * 1024)
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise StorageError(400, "InvalidXmlDocument", "XML declarations are unsupported")
            try:
                xml = ET.fromstring(raw)
            except ET.ParseError:
                raise StorageError(400, "InvalidXmlDocument", "Invalid block-list XML") from None
            if (
                xml.tag != "BlockList"
                or xml.attrib
                or any(
                    n.tag not in {"Latest", "Committed", "Uncommitted"} or n.attrib or len(n)
                    for n in xml
                )
            ):
                raise StorageError(400, "InvalidXmlDocument", "Invalid block-list shape")
            if (
                request.headers.get("content-md5")
                and base64.b64encode(hashlib.md5(raw, usedforsecurity=False).digest()).decode()
                != request.headers["content-md5"]
            ):
                raise StorageError(400, "Md5Mismatch", "Request content MD5 does not match")
            version = await commit_blocks(
                session,
                access,
                container,
                name,
                [(n.tag, n.text or "") for n in xml],
                values | {"properties": properties(request)},
                now,
            )
            row = await session.get(BlobVersion, (access.tenant_id, version.id))
            result = Response(
                status_code=201,
                headers=headers(request)
                | {
                    "ETag": row.etag,
                    "Last-Modified": format_datetime(now, usegmt=True),
                    "x-ms-request-server-encrypted": "true",
                }
                | ({"x-ms-version-id": row.stamp} if settings.get("isVersioningEnabled") else {}),
            )
        else:
            blob = await find_blob(session, access, container, name, now)
            selector = args.get("snapshot", args.get("versionid"))
            kind = "snapshot" if "snapshot" in args else "version"
            version = await selected_version(session, blob, selector, kind)
            if blob is None or (
                version is None and (selector or operation not in {"blocklist", "undelete"})
            ):
                raise StorageError(404, "BlobNotFound", "Blob was not found")
            historical = selector is not None and (
                kind == "snapshot" or version.id != blob.current_id
            )
            if historical and values.get("x-ms-lease-id"):
                raise StorageError(
                    412,
                    "LeaseNotPresentWithBlobOperation",
                    "Historical content has no active lease",
                )
            if operation != "lease" and not historical:
                await require_lease(
                    session,
                    blob,
                    values.get("x-ms-lease-id"),
                    now,
                    write=method not in {"GET", "HEAD"} and operation != "snapshot",
                )
            try:
                check_conditions(version, values, read=method in {"GET", "HEAD"})
            except StorageError as error:
                if error.status != 304 or version is None:
                    raise
                validators = await response_properties(
                    session, request, blob, version, now, settings
                )
                validators.pop("Content-Length", None)
                await session.commit()
                return Response(status_code=304, headers=validators)
            if operation == "get_metadata":
                result = Response(
                    status_code=200,
                    headers=headers(request)
                    | {
                        "ETag": version.etag,
                        "Last-Modified": format_datetime(
                            version.created_at.astimezone(UTC), usegmt=True
                        ),
                        **{
                            "x-ms-meta-" + k: v
                            for k, v in version.properties.get("metadata", {}).items()
                        },
                    },
                )
            elif operation in {"read", "head"}:
                response_headers = await response_properties(
                    session, request, blob, version, now, settings
                )
                response_headers.update(overrides)
                start, end, status = (
                    byte_range(request, version.size)
                    if operation == "read"
                    else (0, version.size, 200)
                )
                response_headers["Content-Length"] = str(end - start)
                if status == 206:
                    response_headers["Content-Range"] = f"bytes {start}-{end - 1}/{version.size}"
                    response_headers.pop("Content-MD5", None)
                if request.headers.get("x-ms-range-get-content-md5"):
                    if (
                        request.headers["x-ms-range-get-content-md5"] != "true"
                        or status != 206
                        or end - start > 4 * 1024 * 1024
                    ):
                        raise StorageError(
                            400, "InvalidHeaderValue", "Range MD5 requires a range of at most 4 MiB"
                        )
                    digest = hashlib.md5(usedforsecurity=False)
                    async for chunk in stream_content(
                        session, access.tenant_id, version.content_id, start, end
                    ):
                        digest.update(chunk)
                    response_headers["Content-MD5"] = base64.b64encode(digest.digest()).decode()
                if operation == "head":
                    result = Response(status_code=200, headers=response_headers)
                else:
                    session = await detached_reader(
                        request, session, access.tenant_id, version.content_id
                    )
                    result = TransactionalStream(
                        stream_content(session, access.tenant_id, version.content_id, start, end),
                        session,
                        status_code=status,
                        headers=response_headers,
                    )
                    streaming = True
            elif operation == "lease":
                if await bounded_body(request):
                    raise StorageError(
                        400, "InvalidRequestBody", "Lease requests require an empty body"
                    )
                action = values.get("x-ms-lease-action")
                status, extra = await operate_lease(session, blob, version, action, values, now)
                await record(
                    session, access, "blob", container + "/" + name, "LeaseBlob:" + str(action), now
                )
                result = Response(
                    status_code=status,
                    headers=headers(request)
                    | extra
                    | {
                        "ETag": version.etag,
                        "Last-Modified": format_datetime(
                            version.created_at.astimezone(UTC), usegmt=True
                        ),
                    },
                )
            elif operation == "snapshot":
                if await bounded_body(request):
                    raise StorageError(
                        400, "InvalidRequestBody", "Snapshot requests require an empty body"
                    )
                content = await object_row(
                    session, access.tenant_id, version.content_id, finalized=True
                )
                snapshot = await new_version(
                    session,
                    blob,
                    reference(content),
                    properties(request, inherit=version.properties),
                    version.blocks,
                    now,
                    kind="snapshot",
                )
                await record(session, access, "blob", container + "/" + name, "SnapshotBlob", now)
                result = Response(
                    status_code=201,
                    headers=headers(request)
                    | {
                        "x-ms-snapshot": snapshot.stamp,
                        "ETag": snapshot.etag,
                        "Last-Modified": format_datetime(now, usegmt=True),
                    },
                )
            elif operation in {"metadata", "properties"}:
                if await bounded_body(request):
                    raise StorageError(
                        400, "InvalidRequestBody", "Property changes require an empty body"
                    )
                from mockazure.providers.storage.routes import metadata_headers

                props = (
                    dict(version.properties, metadata=metadata_headers(request))
                    if operation == "metadata"
                    else dict(properties(request), metadata=version.properties.get("metadata", {}))
                )
                content = await object_row(
                    session, access.tenant_id, version.content_id, finalized=True
                )
                row = await new_version(
                    session, blob, reference(content), props, version.blocks, now
                )
                blob.current_id = row.id
                blob.changed_at = now
                lease = await session.get(BlobLease, (blob.tenant_id, blob.id))
                if lease:
                    lease.etag = row.etag
                if not settings.get("isVersioningEnabled"):
                    await session.delete(version)
                await record(
                    session,
                    access,
                    "blob",
                    container + "/" + name,
                    "SetBlobMetadata" if operation == "metadata" else "SetBlobProperties",
                    now,
                )
                result = Response(
                    status_code=200,
                    headers=headers(request)
                    | {"ETag": row.etag, "Last-Modified": format_datetime(now, usegmt=True)}
                    | (
                        {"x-ms-version-id": row.stamp}
                        if settings.get("isVersioningEnabled")
                        else {}
                    ),
                )
            elif operation == "blocklist":
                mode = args.get("blocklisttype", "committed")
                if mode not in {"committed", "uncommitted", "all"}:
                    raise StorageError(
                        400, "InvalidQueryParameterValue", "Invalid block-list selection"
                    )
                xml = ET.Element("BlockList")
                if mode in {"committed", "all"}:
                    section = ET.SubElement(xml, "CommittedBlocks")
                    for block in version.blocks if version else []:
                        node = ET.SubElement(section, "Block")
                        ET.SubElement(node, "Name").text = block["id"]
                        ET.SubElement(node, "Size").text = str(block["size"])
                if mode in {"uncommitted", "all"} and not selector:
                    section = ET.SubElement(xml, "UncommittedBlocks")
                    for block in await session.scalars(
                        select(BlobBlock)
                        .where(
                            BlobBlock.tenant_id == access.tenant_id, BlobBlock.blob_id == blob.id
                        )
                        .order_by(BlobBlock.block_id)
                    ):
                        node = ET.SubElement(section, "Block")
                        ET.SubElement(node, "Name").text = block.block_id
                        ET.SubElement(node, "Size").text = str(block.size)
                result = xml_response(
                    request,
                    xml,
                    extra={"x-ms-blob-content-length": str(version.size if version else 0)},
                )
            elif operation == "delete":
                snapshots = list(
                    await session.scalars(
                        select(BlobVersion).where(
                            BlobVersion.tenant_id == access.tenant_id,
                            BlobVersion.blob_id == blob.id,
                            BlobVersion.kind == "snapshot",
                            BlobVersion.deleted_at.is_(None),
                        )
                    )
                )
                selection = values.get("x-ms-delete-snapshots")
                if selection not in {None, "include", "only"} or (selector and selection):
                    raise StorageError(
                        400, "InvalidHeaderValue", "Invalid snapshot deletion selection"
                    )
                if snapshots and not selector and not selection:
                    raise StorageError(
                        409,
                        "SnapshotsPresent",
                        "Delete must explicitly include or select snapshots",
                    )
                if selector and version.id == blob.current_id:
                    raise StorageError(
                        409,
                        "OperationNotAllowedOnRootBlob",
                        "Delete the current blob without a version selector",
                    )
                targets = (
                    [version]
                    if selector
                    else snapshots
                    if selection == "only"
                    else snapshots + [version]
                )
                retained = settings.get("deleteRetentionPolicy", {}).get("enabled", False)
                for item in targets:
                    if item.id == blob.current_id:
                        blob.current_id = None
                        if settings.get("isVersioningEnabled"):
                            continue
                    if retained:
                        item.deleted_at = now
                        item.deleted_until = now + timedelta(
                            days=settings["deleteRetentionPolicy"]["days"]
                        )
                    else:
                        await session.delete(item)
                if selection != "only" and not selector:
                    await session.execute(
                        delete(BlobLease).where(
                            BlobLease.tenant_id == access.tenant_id, BlobLease.blob_id == blob.id
                        )
                    )
                blob.changed_at = now
                await record(session, access, "blob", container + "/" + name, "DeleteBlob", now)
                result = Response(status_code=202, headers=headers(request))
            elif operation == "undelete":
                if await bounded_body(request):
                    raise StorageError(400, "InvalidRequestBody", "Undelete requires an empty body")
                rows = list(
                    await session.scalars(
                        select(BlobVersion)
                        .where(
                            BlobVersion.tenant_id == access.tenant_id,
                            BlobVersion.blob_id == blob.id,
                            BlobVersion.deleted_at.is_not(None),
                        )
                        .order_by(BlobVersion.created_at.desc())
                    )
                )

                for item in rows:
                    if item.deleted_until is not None and item.deleted_until > now:
                        item.deleted_at = None
                        item.deleted_until = None
                        if (
                            not settings.get("isVersioningEnabled")
                            and item.kind == "version"
                            and blob.current_id is None
                        ):
                            blob.current_id = item.id
                await record(session, access, "blob", container + "/" + name, "UndeleteBlob", now)
                result = Response(status_code=200, headers=headers(request))
        if not streaming:
            await session.commit()
        return result
    except AdminError as error:
        await session.rollback()
        raise StorageError(error.status, error.code, error.message) from None
    except BaseException:
        await session.rollback()
        raise
    finally:
        if not streaming:
            await session.close()
