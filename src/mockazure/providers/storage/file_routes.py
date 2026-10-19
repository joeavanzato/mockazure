"""Selected Azure FileREST protocol, with distinct path and access rules."""

import base64
import copy
import hashlib
import re
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from urllib.parse import quote
from uuid import UUID, uuid4
from xml.etree.ElementTree import Element, SubElement

from sqlalchemy import select
from starlette.responses import Response

from mockazure.admin.errors import AdminError
from mockazure.cloud.models import CloudState
from mockazure.content.repository import stream_content
from mockazure.identity.keys import read_wrapping_key
from mockazure.providers.storage.blob_routes import TransactionalStream, byte_range, detached_reader
from mockazure.providers.storage.errors import StorageError, headers
from mockazure.storage.authorization import authorize_storage
from mockazure.storage.containers import record, view
from mockazure.storage.file_models import FileCursor, FileNode, FileTree
from mockazure.storage.files import (
    changed,
    check_lease,
    checked_size,
    create_node,
    make_content,
    node_for,
    normalized_path,
    publish_content,
    snapshot_share,
    tree_for,
    write_file_range,
)
from mockazure.storage.lifecycle import lock_accounts
from mockazure.storage.models import StorageAccount, StorageNamespace


def stamp(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "0Z"


def props(request, now, old=None, *, create=False, directory=False):
    from mockazure.providers.storage.routes import metadata_headers

    result = copy.deepcopy(old) if old else {}
    property_headers = {
        "x-ms-content-type": "content_type",
        "x-ms-content-encoding": "content_encoding",
        "x-ms-content-language": "content_language",
        "x-ms-content-disposition": "content_disposition",
        "x-ms-content-md5": "content_md5",
        "x-ms-cache-control": "cache_control",
    }
    if not create and any(name in request.headers for name in property_headers):
        for key in property_headers.values():
            result.pop(key, None)
    if create:
        result["metadata"] = metadata_headers(request)
    for suffix, key in [
        ("type", "content_type"),
        ("encoding", "content_encoding"),
        ("language", "content_language"),
        ("disposition", "content_disposition"),
        ("md5", "content_md5"),
    ]:
        if create and "content-" + suffix in request.headers:
            result[key] = request.headers["content-" + suffix]
        if "x-ms-content-" + suffix in request.headers:
            result[key] = request.headers["x-ms-content-" + suffix]
    if create and "cache-control" in request.headers:
        result["cache_control"] = request.headers["cache-control"]
    if "x-ms-cache-control" in request.headers:
        result["cache_control"] = request.headers["x-ms-cache-control"]
    result.setdefault("content_type", "application/octet-stream")
    if result.get("content_md5"):
        try:
            valid = len(base64.b64decode(result["content_md5"], validate=True)) == 16
        except ValueError:
            valid = False
        if not valid:
            raise StorageError(400, "InvalidHeaderValue", "Invalid file MD5 property")
    for header, key in [
        ("creation", "creation_time"),
        ("last-write", "last_write_time"),
        ("change", "change_time"),
    ]:
        value = request.headers.get(
            "x-ms-file-" + header + "-time", "now" if create or header == "change" else "preserve"
        )
        if value.lower() in {"now", "preserve"}:
            value = value.lower()
        if value == "preserve" and not create:
            result.setdefault(key, stamp(now))
            continue
        try:
            result[key] = (
                stamp(now)
                if value == "now"
                else stamp(datetime.fromisoformat(value.replace("Z", "+00:00")))
            )
        except ValueError:
            raise StorageError(400, "InvalidHeaderValue", "Invalid file timestamp") from None
    attributes = request.headers.get(
        "x-ms-file-attributes", "Directory" if directory else "None" if create else "preserve"
    )
    if attributes.lower() == "preserve":
        attributes = "preserve"
    if attributes != "preserve":
        values = set(attributes.split("|"))
        if values - {
            "None",
            "ReadOnly",
            "Hidden",
            "System",
            "Directory",
            "Archive",
            "Temporary",
            "Offline",
            "NotContentIndexed",
            "NoScrubData",
        }:
            raise StorageError(400, "InvalidHeaderValue", "Unsupported file attributes")
        result["attributes"] = attributes
    permission = request.headers.get("x-ms-file-permission")
    permission = permission.lower() if permission else permission
    if permission not in {None, "inherit", "preserve"} or (create and permission == "preserve"):
        raise StorageError(
            400, "InvalidHeaderValue", "Only inherited default permissions are supported"
        )
    return result


def node_headers(request, row):
    values = headers(request) | {
        "ETag": row.etag,
        "Last-Modified": format_datetime(row.changed_at.astimezone(UTC), usegmt=True),
        "x-ms-file-id": str(row.public_id.int & ((1 << 64) - 1)),
        "x-ms-file-attributes": row.properties.get(
            "attributes", "Directory" if row.directory else "None"
        ),
        "x-ms-file-creation-time": row.properties.get("creation_time", stamp(row.created_at)),
        "x-ms-file-last-write-time": row.properties.get("last_write_time", stamp(row.changed_at)),
        "x-ms-file-change-time": row.properties.get("change_time", stamp(row.changed_at)),
        "x-ms-lease-status": "locked" if row.lease_state == "leased" else "unlocked",
        "x-ms-lease-state": row.lease_state,
        "x-ms-server-encrypted": "true",
    }
    if row.lease_state == "leased":
        values["x-ms-lease-duration"] = "infinite"
    values.update({"x-ms-meta-" + k: v for k, v in row.properties.get("metadata", {}).items()})
    for key, header in [
        ("content_type", "Content-Type"),
        ("content_encoding", "Content-Encoding"),
        ("content_language", "Content-Language"),
        ("content_disposition", "Content-Disposition"),
        ("content_md5", "Content-MD5"),
        ("cache_control", "Cache-Control"),
    ]:
        if key in row.properties:
            values[header] = row.properties[key]
    return values


def xml_name(element, value, attribute=None):
    encoded = any(c in value for c in ("\ufffe", "\uffff"))
    if encoded:
        value = quote(value, safe="")
        element.set("Encoded", "true")
    if attribute:
        element.set(attribute, value)
    else:
        element.text = value


async def directory_listing(session, request, tree, row, now):
    from mockazure.providers.storage.routes import xml_response

    args = request.query_params
    maximum = min(5000, int(args.get("maxresults", "5000")))
    if maximum < 1:
        raise StorageError(400, "InvalidQueryParameterValue", "Invalid page size")
    include = set(args.get("include", "").lower().split(",")) - {""}
    if include - {"timestamps", "etag", "attributes", "permissionkey"}:
        raise StorageError(400, "InvalidQueryParameterValue", "Unsupported listing include")
    if "permissionkey" in include:
        raise StorageError(
            400, "InvalidQueryParameterValue", "Custom file permissions are not supported"
        )
    if request.headers.get("x-ms-file-extended-info") not in {None, "true"}:
        raise StorageError(400, "InvalidHeaderValue", "Extended info must be true")
    prefix = args.get("prefix", "").casefold()
    query = {
        "directory": row.path_key,
        "prefix": prefix,
        "maxresults": maximum,
        "include": sorted(include),
    }
    generation = (await session.get(CloudState, tree.tenant_id)).generation
    statement = select(FileNode).where(
        FileNode.tenant_id == tree.tenant_id,
        FileNode.tree_id == tree.id,
        FileNode.parent_hash == row.path_hash,
        FileNode.id != row.id,
    )
    if prefix:
        statement = statement.where(
            FileNode.path_key.startswith(
                (row.path_key + "/" if row.path_key else "") + prefix, autoescape=True
            )
        )
    if args.get("marker"):
        try:
            cursor = await session.get(FileCursor, (tree.tenant_id, UUID(args["marker"])))
        except ValueError:
            cursor = None
        if (
            cursor is None
            or cursor.tree_id != tree.id
            or cursor.generation != generation
            or cursor.query != query
            or cursor.expires_at <= now
        ):
            raise StorageError(400, "InvalidMarker", "File continuation is invalid or expired")
        statement = statement.where(FileNode.path_key.collate("C") > cursor.last_key)
    rows = list(
        await session.scalars(statement.order_by(FileNode.path_key.collate("C")).limit(maximum + 1))
    )
    xml = Element(
        "EnumerationResults",
        {
            "ServiceEndpoint": str(request.url).split("?")[0].split("/" + tree.share)[0] + "/",
            "ShareName": tree.share,
            "DirectoryPath": row.path,
        },
    )
    xml_name(xml, row.path, "DirectoryPath")
    if tree.stamp:
        xml.set("ShareSnapshot", tree.stamp)
    SubElement(xml, "DirectoryId").text = str(row.public_id.int & ((1 << 64) - 1))
    for tag, value in [
        ("Prefix", args.get("prefix", "")),
        ("Marker", args.get("marker", "")),
        ("MaxResults", str(maximum)),
    ]:
        xml_name(SubElement(xml, tag), value)
    entries = SubElement(xml, "Entries")
    for item in rows[:maximum]:
        entry = SubElement(entries, "Directory" if item.directory else "File")
        xml_name(SubElement(entry, "Name"), item.path.rsplit("/", 1)[-1])
        SubElement(entry, "FileId").text = str(item.public_id.int & ((1 << 64) - 1))
        properties = SubElement(entry, "Properties")
        if not item.directory:
            SubElement(properties, "Content-Length").text = str(item.size)
        if "etag" in include:
            SubElement(properties, "Etag").text = item.etag
        if "attributes" in include:
            SubElement(entry, "Attributes").text = item.properties.get(
                "attributes", "Directory" if item.directory else "None"
            )
        if "timestamps" in include:
            SubElement(properties, "Last-Modified").text = format_datetime(
                item.changed_at.astimezone(UTC), usegmt=True
            )
            for tag, key in [
                ("CreationTime", "creation_time"),
                ("LastAccessTime", "last_write_time"),
                ("LastWriteTime", "last_write_time"),
                ("ChangeTime", "change_time"),
            ]:
                SubElement(properties, tag).text = item.properties.get(key, stamp(item.changed_at))
    marker = ""
    if len(rows) > maximum:
        cursor = FileCursor(
            tenant_id=tree.tenant_id,
            tree_id=tree.id,
            generation=generation,
            query=query,
            last_key=rows[maximum - 1].path_key,
            expires_at=now + timedelta(hours=1),
        )
        session.add(cursor)
        await session.flush()
        marker = str(cursor.id)
    SubElement(xml, "NextMarker").text = marker
    return xml_response(request, xml)


def lease_operation(row, request):
    action = request.headers.get("x-ms-lease-action")
    supplied = request.headers.get("x-ms-lease-id")
    proposed = request.headers.get("x-ms-proposed-lease-id")
    try:
        supplied = UUID(supplied) if supplied else None
        proposed = UUID(proposed) if proposed else None
    except ValueError:
        raise StorageError(400, "InvalidHeaderValue", "Invalid lease ID") from None
    if action == "acquire":
        if request.headers.get("x-ms-lease-duration") != "-1":
            raise StorageError(400, "InvalidHeaderValue", "File leases require infinite duration")
        if row.lease_state == "leased" and proposed != row.lease_id:
            raise StorageError(409, "LeaseAlreadyPresent", "File already has an active lease")
        row.lease_id = proposed or uuid4()
        row.lease_state = "leased"
        return 201, {"x-ms-lease-id": str(row.lease_id)}
    if action == "break":
        if row.lease_state == "available":
            raise StorageError(409, "LeaseNotPresentWithLeaseOperation", "No lease is present")
        row.lease_state = "broken"
        return 202, {"x-ms-lease-time": "0"}
    if row.lease_state == "available" or supplied != row.lease_id:
        raise StorageError(409, "LeaseIdMismatchWithLeaseOperation", "Lease ID does not match")
    if action == "release":
        row.lease_id = None
        row.lease_state = "available"
        return 200, {}
    if action == "change" and row.lease_state == "leased" and proposed:
        row.lease_id = proposed
        return 200, {"x-ms-lease-id": str(proposed)}
    raise StorageError(400, "InvalidHeaderValue", "Unsupported file lease operation")


async def dispatch_file(request, account, share, raw_path):
    from mockazure.providers.storage.routes import (
        bounded_body,
        metadata_headers,
        properties_headers,
        xml_response,
    )

    args = request.query_params
    method = request.method
    comp = args.get("comp")
    restype = args.get("restype")
    now = datetime.now(UTC)
    version = request.headers.get("x-ms-version", args.get("api-version", args.get("sv")))
    if version not in {"2019-07-07", "2025-01-05", "2025-11-05"}:
        raise StorageError(
            400, "InvalidHeaderValue", "File content profile requires version 2025-11-05"
        )
    share_operation = restype == "share" and not raw_path
    directory = restype == "directory"
    if share_operation:
        operation = (
            "snapshot"
            if comp == "snapshot" and method == "PUT"
            else "share_delete"
            if comp is None and method == "DELETE"
            else "share_read"
            if comp is None and method in {"GET", "HEAD"}
            else None
        )
    elif directory:
        operation = (
            "list"
            if comp == "list" and method == "GET"
            else "metadata"
            if comp == "metadata" and method == "PUT"
            else "properties"
            if comp == "properties" and method == "PUT"
            else "get_metadata"
            if comp == "metadata" and method in {"GET", "HEAD"}
            else {"PUT": "create", "GET": "head", "HEAD": "head", "DELETE": "delete"}.get(method)
            if comp is None
            else None
        )
    else:
        operation = (
            "range"
            if comp == "range" and method == "PUT"
            else "rangelist"
            if comp == "rangelist" and method == "GET"
            else comp
            if comp in {"metadata", "properties", "lease"} and method == "PUT"
            else "get_metadata"
            if comp == "metadata" and method in {"GET", "HEAD"}
            else {"PUT": "create", "GET": "read", "HEAD": "head", "DELETE": "delete"}.get(method)
            if comp is None
            else None
        )
    if not operation:
        raise StorageError(
            501, "MockAzureOperationNotImplemented", "FileREST operation is not implemented"
        )
    if version == "2025-01-05" and (
        operation not in {"read", "head", "get_metadata", "share_delete"}
        or "sharesnapshot" in args
        or not request.headers.get("Authorization", "").startswith(("SharedKey ", "SharedKeyLite "))
    ):
        raise StorageError(
            400, "InvalidHeaderValue", "Unsupported 2025-01 FileREST content profile"
        )
    if version == "2019-07-07":
        if (
            operation
            not in {
                "create",
                "read",
                "head",
                "range",
                "rangelist",
                "properties",
                "metadata",
                "get_metadata",
                "delete",
                "share_delete",
            }
            or "sharesnapshot" in args
            or not request.headers.get("Authorization", "").startswith(
                ("SharedKey ", "SharedKeyLite ")
            )
            or any(
                key in request.headers
                for key in {
                    "x-ms-file-change-time",
                    "x-ms-file-request-intent",
                    "x-ms-allow-trailing-dot",
                }
            )
            or (operation == "range" and "x-ms-file-last-write-time" in request.headers)
        ):
            raise StorageError(400, "InvalidHeaderValue", "Unsupported legacy FileREST profile")
        if operation in {"create", "properties"} and not {
            "x-ms-file-permission",
            "x-ms-file-attributes",
            "x-ms-file-creation-time",
            "x-ms-file-last-write-time",
        } <= set(request.headers):
            raise StorageError(
                400, "MissingRequiredHeader", "Legacy FileREST requires explicit file properties"
            )
    auth_keys = {"sv", "ss", "srt", "sp", "st", "se", "sip", "spr", "sig", "sr", "si"}
    allowed = {"comp", "timeout", "api-version", "sharesnapshot"} | auth_keys
    if directory or share_operation:
        allowed.add("restype")
    if operation == "list":
        allowed |= {"prefix", "marker", "maxresults", "include"}
    if set(args) - allowed:
        raise StorageError(400, "InvalidQueryParameterValue", "Unsupported FileREST parameter")
    if args.get("sharesnapshot") and method not in {"GET", "HEAD"} and operation != "share_delete":
        raise StorageError(400, "InvalidQueryParameterValue", "Share snapshots are read-only")
    common = {
        "x-ms-date",
        "x-ms-version",
        "x-ms-client-request-id",
        "x-ms-return-client-request-id",
        "x-ms-allow-trailing-dot",
        "x-ms-file-request-intent",
    }
    content_properties = {
        "x-ms-content-type",
        "x-ms-content-md5",
        "x-ms-content-encoding",
        "x-ms-content-language",
        "x-ms-content-disposition",
        "x-ms-cache-control",
    }
    file_properties = {
        "x-ms-file-attributes",
        "x-ms-file-creation-time",
        "x-ms-file-last-write-time",
        "x-ms-file-change-time",
        "x-ms-file-permission",
    }
    specific = {
        "create": file_properties
        | (
            set()
            if directory
            else content_properties | {"x-ms-type", "x-ms-content-length", "x-ms-lease-id"}
        ),
        "properties": file_properties
        | (set() if directory else content_properties | {"x-ms-content-length", "x-ms-lease-id"}),
        "metadata": set() if directory else {"x-ms-lease-id"},
        "get_metadata": set() if directory else {"x-ms-lease-id"},
        "head": set() if directory else {"x-ms-lease-id"},
        "read": {"x-ms-range", "x-ms-range-get-content-md5", "x-ms-lease-id"},
        "range": {"x-ms-range", "x-ms-write", "x-ms-lease-id", "x-ms-file-last-write-time"},
        "rangelist": {"x-ms-range", "x-ms-lease-id"},
        "delete": set() if directory else {"x-ms-lease-id"},
        "lease": {"x-ms-lease-action"},
        "list": {"x-ms-file-extended-info"},
        "snapshot": set(),
        "share_read": set(),
        "share_delete": {"x-ms-delete-snapshots"},
    }[operation]
    if operation == "lease":
        specific |= {
            "acquire": {"x-ms-lease-duration", "x-ms-proposed-lease-id"},
            "release": {"x-ms-lease-id"},
            "change": {"x-ms-lease-id", "x-ms-proposed-lease-id"},
            "break": {"x-ms-lease-id"},
        }.get(request.headers.get("x-ms-lease-action"), set())
    for key in request.headers:
        if key.startswith("if-") or (
            key.startswith("x-ms-")
            and key not in common | specific
            and not (
                operation in {"create", "metadata", "snapshot"}
                and (key.startswith("x-ms-meta-") or key == "x-ms-meta")
            )
        ):
            raise StorageError(400, "InvalidHeaderValue", "Unsupported FileREST behavior header")
    trailing = request.headers.get("x-ms-allow-trailing-dot", "false")
    if trailing not in {"true", "false"}:
        raise StorageError(400, "InvalidHeaderValue", "Invalid trailing-dot option")
    path = normalized_path(raw_path, directory=directory, trailing=trailing == "true")
    action = (
        "delete"
        if operation == "share_delete"
        else "read"
        if operation == "share_read"
        else "create"
        if operation == "snapshot"
        else "list_files"
        if operation == "list"
        else "read_file"
        if method in {"GET", "HEAD"}
        else "delete_file"
        if operation == "delete"
        else "write_file"
    )
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
            session, request, "file", action, share + ("/" + raw_path if raw_path else ""), now
        )
        if share_operation:
            namespace = await session.get(
                StorageNamespace, (access.tenant_id, access.account_id, "file", share)
            )
            if namespace is None:
                raise StorageError(404, "ShareNotFound", "Share was not found")
            if operation == "snapshot":
                if await bounded_body(request):
                    raise StorageError(
                        400, "InvalidRequestBody", "Share snapshot requires an empty body"
                    )
                tree = await snapshot_share(
                    session,
                    access,
                    share,
                    metadata_headers(request)
                    if any(k.startswith("x-ms-meta-") for k in request.headers)
                    else None,
                    now,
                )
                result = Response(
                    status_code=201,
                    headers=headers(request)
                    | properties_headers(view(namespace), "file")
                    | {"x-ms-snapshot": tree.stamp},
                )
            elif operation == "share_read":
                tree = await tree_for(
                    session, access, share, now, args.get("sharesnapshot", ""), create=True
                )
                item = view(namespace) | {
                    "properties": tree.properties,
                    "changed_at": tree.created_at,
                }
                result = Response(
                    status_code=200, headers=headers(request) | properties_headers(item, "file")
                )
            else:
                if args.get("sharesnapshot"):
                    tree = await tree_for(session, access, share, now, args["sharesnapshot"])
                    await session.delete(tree)
                else:
                    snapshots = await session.scalar(
                        select(FileTree.id)
                        .where(
                            FileTree.tenant_id == access.tenant_id,
                            FileTree.account_id == access.account_id,
                            FileTree.share == share,
                            FileTree.stamp != "",
                        )
                        .limit(1)
                    )
                    if request.headers.get("x-ms-delete-snapshots") not in {None, "include"}:
                        raise StorageError(
                            400, "InvalidHeaderValue", "Invalid share snapshot delete mode"
                        )
                    if snapshots and request.headers.get("x-ms-delete-snapshots") != "include":
                        raise StorageError(
                            409, "ShareHasSnapshots", "Delete must include share snapshots"
                        )
                    await session.delete(namespace)
                await record(
                    session,
                    access,
                    "file",
                    share,
                    "DeleteShareSnapshot" if args.get("sharesnapshot") else "DeleteShare",
                    now,
                )
                result = Response(status_code=202, headers=headers(request))
        else:
            tree = await tree_for(
                session, access, share, now, args.get("sharesnapshot", ""), create=True
            )
            if operation == "create":
                if await bounded_body(request):
                    raise StorageError(
                        400, "InvalidRequestBody", "File creation initializes an empty sparse file"
                    )
                if not directory and request.headers.get("x-ms-type", "").lower() != "file":
                    raise StorageError(400, "InvalidHeaderValue", "x-ms-type must be file")
                old = await node_for(session, tree, path, required=False)
                if old is not None and "ReadOnly" in old.properties.get("attributes", "").split(
                    "|"
                ):
                    raise StorageError(409, "ReadOnlyAttribute", "File is marked read-only")
                if (
                    access.mode.endswith("_sas")
                    and "w" not in access.permissions
                    and old is not None
                ):
                    raise StorageError(
                        403,
                        "AuthorizationPermissionMismatch",
                        "Overwrite requires write permission",
                    )
                row = await create_node(
                    session,
                    access,
                    share,
                    path,
                    directory,
                    request.headers.get("x-ms-content-length") if not directory else 0,
                    props(request, now, create=True, directory=directory),
                    dict(request.headers),
                    now,
                )
                result = Response(status_code=201, headers=node_headers(request, row))
            else:
                row = await node_for(session, tree, path)
                if row.directory != directory:
                    raise StorageError(
                        404, "ResourceNotFound", "The selected resource kind was not found"
                    )
                if (
                    access.mode.endswith("_sas")
                    and action == "write_file"
                    and "w" not in access.permissions
                ):
                    raise StorageError(
                        403, "AuthorizationPermissionMismatch", "Mutation requires write permission"
                    )
                if operation != "lease" and not directory:
                    check_lease(
                        row,
                        request.headers.get("x-ms-lease-id"),
                        write=method not in {"GET", "HEAD"},
                    )
                if (
                    method not in {"GET", "HEAD"}
                    and operation not in {"lease", "properties"}
                    and "ReadOnly" in row.properties.get("attributes", "").split("|")
                ):
                    raise StorageError(409, "ReadOnlyAttribute", "File is marked read-only")
                if operation == "list":
                    result = await directory_listing(session, request, tree, row, now)
                elif operation in {"read", "head", "get_metadata"}:
                    await record(
                        session,
                        access,
                        "file",
                        share + "/" + path,
                        "GetFile"
                        if operation == "read"
                        else "GetDirectoryProperties"
                        if directory
                        else "GetFileProperties",
                        now,
                    )
                    result_headers = node_headers(request, row)
                    if operation == "get_metadata":
                        result_headers = {
                            k: v
                            for k, v in result_headers.items()
                            if k.lower()
                            in {"etag", "last-modified", "x-ms-version", "x-ms-request-id"}
                            or k.startswith("x-ms-meta-")
                        }
                        result = Response(status_code=200, headers=result_headers)
                    elif operation == "head":
                        result_headers["Content-Length"] = str(row.size if not directory else 0)
                        result = Response(status_code=200, headers=result_headers)
                    else:
                        start, end, status = byte_range(request, row.size)
                        result_headers["Content-Length"] = str(end - start)
                        if status == 206:
                            result_headers["Content-Range"] = f"bytes {start}-{end - 1}/{row.size}"
                            result_headers.pop("Content-MD5", None)
                        if request.headers.get("x-ms-range-get-content-md5"):
                            if (
                                request.headers["x-ms-range-get-content-md5"] != "true"
                                or status != 206
                                or end - start > 4194304
                            ):
                                raise StorageError(
                                    400, "InvalidHeaderValue", "Range MD5 requires at most 4 MiB"
                                )
                            md5 = hashlib.md5(usedforsecurity=False)
                            async for part in stream_content(
                                session, access.tenant_id, row.content_id, start, end
                            ):
                                md5.update(part)
                            result_headers["Content-MD5"] = base64.b64encode(md5.digest()).decode()
                        session = await detached_reader(
                            request, session, access.tenant_id, row.content_id
                        )
                        result = TransactionalStream(
                            stream_content(session, access.tenant_id, row.content_id, start, end),
                            session,
                            status_code=status,
                            headers=result_headers,
                        )
                        streaming = True
                elif operation == "range":
                    match = re.fullmatch(
                        r"bytes=(\d+)-(\d+)",
                        request.headers.get("x-ms-range", request.headers.get("range", "")),
                    )
                    if not match:
                        raise StorageError(
                            400, "InvalidRange", "An explicit byte range is required"
                        )
                    start, end = int(match[1]), int(match[2]) + 1
                    mode = request.headers.get("x-ms-write", "").lower()
                    data = await bounded_body(request, 4194304)
                    if mode not in {"update", "clear"} or (mode == "clear" and data):
                        raise StorageError(400, "InvalidHeaderValue", "Invalid range operation")
                    if request.headers.get("content-md5") and (
                        mode == "clear"
                        or base64.b64encode(
                            hashlib.md5(data, usedforsecurity=False).digest()
                        ).decode()
                        != request.headers["content-md5"]
                    ):
                        raise StorageError(400, "Md5Mismatch", "Range MD5 does not match")
                    await write_file_range(
                        session,
                        access,
                        share,
                        path,
                        start,
                        data if mode == "update" else None,
                        end - start,
                        dict(request.headers),
                        now,
                    )
                    last = request.headers.get("x-ms-file-last-write-time", "now").lower()
                    if last not in {"now", "preserve"}:
                        raise StorageError(
                            400, "InvalidHeaderValue", "Unsupported range last-write value"
                        )
                    if last == "now":
                        row.properties = dict(row.properties, last_write_time=stamp(now))
                    result = Response(status_code=201, headers=node_headers(request, row))
                elif operation == "rangelist":
                    start, end, _ = byte_range(request, row.size)
                    xml = Element("Ranges")
                    for low, high in row.ranges:
                        low, high = max(start, low), min(end, high)
                        if low < high:
                            item = SubElement(xml, "Range")
                            SubElement(item, "Start").text = str(low)
                            SubElement(item, "End").text = str(high - 1)
                    result = xml_response(
                        request, xml, extra={"ETag": row.etag, "x-ms-content-length": str(row.size)}
                    )
                elif operation in {"metadata", "properties"}:
                    if await bounded_body(request):
                        raise StorageError(
                            400, "InvalidRequestBody", "Property updates require an empty body"
                        )
                    if operation == "metadata":
                        row.properties = dict(row.properties, metadata=metadata_headers(request))
                        changed(row, now)
                    else:
                        values = props(request, now, row.properties, directory=directory)
                        if "x-ms-content-length" in request.headers:
                            size = checked_size(request.headers["x-ms-content-length"])
                            ref = await make_content(
                                session,
                                access.tenant_id,
                                row,
                                size,
                                values.get("content_type", "application/octet-stream"),
                            )
                            await publish_content(session, row, ref, now)
                            if (
                                request.headers.get("x-ms-file-last-write-time", "preserve")
                                == "preserve"
                            ):
                                values["last_write_time"] = stamp(now)
                            row.ranges = [
                                [low, min(high, size)] for low, high in row.ranges if low < size
                            ]
                        row.properties = values
                        changed(row, now)
                    await record(
                        session,
                        access,
                        "file",
                        share + "/" + path,
                        "SetDirectoryMetadata"
                        if directory and operation == "metadata"
                        else "SetDirectoryProperties"
                        if directory
                        else "SetFileMetadata"
                        if operation == "metadata"
                        else "SetFileProperties",
                        now,
                    )
                    result = Response(status_code=200, headers=node_headers(request, row))
                elif operation == "lease":
                    if await bounded_body(request):
                        raise StorageError(
                            400, "InvalidRequestBody", "Lease requires an empty body"
                        )
                    status, extra = lease_operation(row, request)
                    result = Response(
                        status_code=status, headers=node_headers(request, row) | extra
                    )
                    await record(
                        session,
                        access,
                        "file",
                        share + "/" + path,
                        "LeaseFile:" + request.headers["x-ms-lease-action"],
                        now,
                    )
                elif operation == "delete":
                    if not path:
                        raise StorageError(
                            400,
                            "InvalidResourceName",
                            "The share root cannot be deleted as a directory",
                        )
                    if directory and await session.scalar(
                        select(FileNode.id)
                        .where(
                            FileNode.tenant_id == access.tenant_id,
                            FileNode.tree_id == tree.id,
                            FileNode.parent_hash == row.path_hash,
                        )
                        .limit(1)
                    ):
                        raise StorageError(409, "DirectoryNotEmpty", "Directory must be empty")
                    await session.delete(row)
                    await record(
                        session,
                        access,
                        "file",
                        share + "/" + path,
                        "DeleteDirectory" if directory else "DeleteFile",
                        now,
                    )
                    result = Response(status_code=202, headers=headers(request))
        if not streaming:
            if request.method in {"GET", "HEAD"} and operation not in {
                "read",
                "head",
                "get_metadata",
            }:
                await record(
                    session,
                    access,
                    "file",
                    share + "/" + path,
                    "ListFilesAndDirectories" if operation == "list" else "GetFileProperties",
                    now,
                )
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
