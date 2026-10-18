"""Storage data listeners preserve wire paths and own their service errors."""

from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import urlsplit
from uuid import uuid4
from xml.etree.ElementTree import Element, SubElement, tostring

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response

from mockazure.identity.keys import read_wrapping_key
from mockazure.providers.arm.models import ArmResource
from mockazure.providers.storage.errors import StorageError, headers, response
from mockazure.providers.storage.transport import buffered_request
from mockazure.storage.authorization import authorize_storage
from mockazure.storage.containers import create_container, record, view
from mockazure.storage.endpoints import SERVICES, listener_ports
from mockazure.storage.lifecycle import lock_accounts
from mockazure.storage.models import StorageAccount, StorageNamespace
from mockazure.storage.policies import delegation_key, parse_policies
from mockazure.storage.shares import create_share


def service_for_request(request, settings):
    port = (request.scope.get("server") or ("", None))[1]
    ports = listener_ports(settings)[1:]
    if port in ports:
        return ("blob", "file", "queue", "table", "keyvault")[ports.index(port)]
    # Explicit published host ports can differ from container listen ports.
    # A live listener always wins; ASGI test clients supply the public URL port.
    if port == settings.listen_port:
        return None
    authority = request.headers.get("host", "").lower()
    for profile in (settings.endpoints, *settings.client_profiles.values()):
        for service, name in SERVICES.items():
            if authority == urlsplit(getattr(profile, name)).netloc.lower():
                return service
    return None


def xml_response(request, root, status=200, extra=None):
    return Response(
        tostring(root, encoding="utf-8", xml_declaration=True),
        status_code=status,
        media_type="application/xml",
        headers=headers(request) | (extra or {}),
    )


def properties_headers(item, service):
    result = {
        "ETag": item["etag"],
        "Last-Modified": format_datetime(item["changed_at"].astimezone(UTC), usegmt=True),
    }
    result.update({"x-ms-meta-" + k: v for k, v in item["properties"].get("metadata", {}).items()})
    if service == "blob":
        if item["properties"].get("public_access"):
            result["x-ms-blob-public-access"] = item["properties"]["public_access"]
        result.update(
            {
                "x-ms-lease-status": "unlocked",
                "x-ms-lease-state": "available",
                "x-ms-has-immutability-policy": "false",
                "x-ms-has-legal-hold": "false",
            }
        )
    else:
        result.update(
            {
                "x-ms-share-quota": str(item["properties"].get("quota", 5120)),
                "x-ms-enabled-protocols": "SMB",
            }
        )
    return result


async def handle(request, service):
    try:
        async with buffered_request(request) as buffered:
            return await dispatch(buffered, service)
    except StorageError as error:
        return response(request, error, service)
    except SQLAlchemyError:
        return response(
            request, StorageError(503, "ServerBusy", "Storage database is unavailable"), service
        )
    except (ValueError, TypeError, UnicodeError):
        return response(
            request,
            StorageError(400, "InvalidQueryParameterValue", "Invalid Storage request"),
            service,
        )


async def dispatch(request, service):
    if service == "queue":
        from mockazure.providers.storage.queue_routes import dispatch_queue

        return await dispatch_queue(request)
    if service == "table":
        from mockazure.providers.storage.table_routes import dispatch_table

        return await dispatch_table(request)
    if service not in {"blob", "file"}:
        raise StorageError(
            501,
            "MockAzureOperationNotImplemented",
            "This data service operation is not implemented",
        )
    profile = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
    if (
        request.headers.get("host", "").lower()
        != urlsplit(getattr(profile, SERVICES[service])).netloc.lower()
    ):
        raise StorageError(400, "InvalidUri", "The Host does not match the service listener")
    args = request.query_params
    if len(args) != len(args.multi_items()):
        raise StorageError(400, "InvalidQueryParameterValue", "Duplicate query parameter")
    version = request.headers.get("x-ms-version", args.get("api-version", args.get("sv")))
    if version is None and not request.headers.get("Authorization") and "sig" not in args:
        version = "2025-11-05"
    if version not in {"2019-07-07", "2025-01-05", "2025-11-05"}:
        raise StorageError(400, "InvalidHeaderValue", "Unsupported Storage request version")
    content_parts = request.scope["path"].lstrip("/").split("/", 2)
    if service == "blob" and (
        (len(content_parts) == 3 and content_parts[1] and content_parts[2])
        or (
            len(content_parts) == 2
            and args.get("restype") == "container"
            and args.get("comp") == "list"
        )
    ):
        from mockazure.providers.storage.blob_routes import dispatch_blob

        return await dispatch_blob(
            request,
            content_parts[0],
            content_parts[1],
            content_parts[2] if len(content_parts) == 3 else "",
        )
    if (
        service == "file"
        and len(content_parts) >= 2
        and content_parts[1]
        and (
            (len(content_parts) == 3 and content_parts[2])
            or args.get("restype") == "directory"
            or "sharesnapshot" in args
            or args.get("comp") == "snapshot"
            or (args.get("restype") == "share" and request.method == "DELETE")
        )
    ):
        from mockazure.providers.storage.file_routes import dispatch_file

        return await dispatch_file(
            request,
            content_parts[0],
            content_parts[1],
            content_parts[2] if len(content_parts) == 3 else "",
        )
    parts = request.scope["path"].strip("/").split("/")
    if len(parts) > 2 or not parts or not parts[0]:
        raise StorageError(
            501, "MockAzureOperationNotImplemented", "This Storage operation is not implemented"
        )
    account = parts[0]
    name = parts[1] if len(parts) == 2 else ""
    kind = "container" if service == "blob" else "share"
    comp = args.get("comp")
    if (
        not name
        and comp == "userdelegationkey"
        and args.get("restype") == "service"
        and request.method == "POST"
        and service == "blob"
    ):
        if version != "2025-11-05":
            raise StorageError(
                400, "InvalidHeaderValue", "Delegation keys require version 2025-11-05"
            )
        action = "delegation"
    elif (
        name and args.get("restype") == kind and comp == "acl" and request.method in {"GET", "PUT"}
    ):
        action = "get_acl" if request.method == "GET" else "set_acl"
    elif (
        name
        and args.get("restype") == kind
        and comp == "metadata"
        and request.method in {"GET", "HEAD", "PUT"}
    ):
        action = "metadata" if request.method == "PUT" else "get_metadata"
    elif not name and comp == "list" and request.method == "GET":
        action = "list"
    elif (
        name
        and args.get("restype") == kind
        and comp is None
        and request.method in {"PUT", "GET", "HEAD", "DELETE"}
    ):
        action = {"PUT": "create", "GET": "read", "HEAD": "read", "DELETE": "delete"}[
            request.method
        ]
    else:
        raise StorageError(
            501, "MockAzureOperationNotImplemented", "This Storage operation is not implemented"
        )
    allowed = {
        "restype",
        "comp",
        "timeout",
        "maxresults",
        "marker",
        "prefix",
        "include",
        "api-version",
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
        "scid",
        "rscc",
        "rscd",
        "rsce",
        "rscl",
        "rsct",
    }
    includes = set(args.get("include", "").lower().split(",")) - {""}
    supported_includes = {"metadata", "snapshots"} if service == "file" else {"metadata"}
    if set(args) - allowed or includes - supported_includes:
        raise StorageError(400, "InvalidQueryParameterValue", "Unsupported Storage query option")
    supported_headers = {
        "x-ms-meta",
        "x-ms-date",
        "x-ms-version",
        "x-ms-client-request-id",
        "x-ms-return-client-request-id",
        "x-ms-lease-id",
        "x-ms-blob-public-access",
        "x-ms-share-quota",
        "x-ms-file-request-intent",
    }
    for key, value in request.headers.items():
        if (
            key.startswith("x-ms-")
            and key not in supported_headers
            and not key.startswith("x-ms-meta-")
        ):
            if key == "x-ms-enabled-protocols" and service == "file" and value == "SMB":
                continue
            raise StorageError(400, "InvalidHeaderValue", "This Storage header is not supported")
    now = datetime.now(UTC)
    async with request.app.state.database.sessions.begin() as session:
        session.info["storage_cipher"] = read_wrapping_key(request.app.state.settings.data_dir)
        owner = await session.scalar(
            select(StorageAccount.tenant_id).where(StorageAccount.name == account)
        )
        if owner is None:
            raise StorageError(404, "ResourceNotFound", "Storage account was not found")
        await lock_accounts(session, owner)
        now = datetime.now(UTC)
        access = await authorize_storage(session, request, service, action, name, now)
        if action == "delegation":
            raw = await bounded_body(request)
            result = await delegation_key(session, access, raw, now)
            xml = Element("UserDelegationKey")
            for key, value in result.items():
                SubElement(xml, key).text = value
            return xml_response(request, xml)
        if action == "create":
            if await bounded_body(request):
                raise StorageError(
                    400, "InvalidRequestBody", "This operation requires an empty body"
                )
            meta = metadata_headers(request)
            properties = {"metadata": meta}
            if service == "file":
                properties["quota"] = int(request.headers.get("x-ms-share-quota", "5120"))
                if not 1 <= properties["quota"] <= 102400:
                    raise StorageError(400, "InvalidHeaderValue", "Invalid share quota")
            if service == "blob":
                arm = await session.get(ArmResource, (access.tenant_id, access.account_id))
                public = public_access(request, arm)
                if public:
                    properties["public_access"] = public
            item = await (create_container if service == "blob" else create_share)(
                session, access, name, properties, now
            )
            return Response(
                status_code=201, headers=headers(request) | properties_headers(item, service)
            )
        if action == "list":
            if service == "file" and "snapshots" in includes:
                from mockazure.storage.file_listing import list_share_snapshots

                result = await list_share_snapshots(session, request, access, now)
                if result is not None:
                    return result
            maximum = int(args.get("maxresults", "5000"))
            if not 1 <= maximum <= 5000:
                raise StorageError(400, "InvalidQueryParameterValue", "maxresults must be 1..5000")
            rows = list(
                await session.scalars(
                    select(StorageNamespace)
                    .where(
                        StorageNamespace.tenant_id == access.tenant_id,
                        StorageNamespace.account_id == access.account_id,
                        StorageNamespace.service == service,
                        StorageNamespace.name > args.get("marker", ""),
                        StorageNamespace.name.startswith(args.get("prefix", ""), autoescape=True),
                    )
                    .order_by(StorageNamespace.name)
                    .limit(maximum + 1)
                )
            )
            root = Element(
                "EnumerationResults", {"ServiceEndpoint": str(request.url).split("?")[0]}
            )
            for tag, value in [
                ("Prefix", args.get("prefix", "")),
                ("Marker", args.get("marker", "")),
                ("MaxResults", str(maximum)),
            ]:
                SubElement(root, tag).text = value
            group = SubElement(root, "Containers" if service == "blob" else "Shares")
            for row in rows[:maximum]:
                item = SubElement(group, kind.title())
                SubElement(item, "Name").text = row.name
                props = SubElement(item, "Properties")
                SubElement(props, "Last-Modified").text = format_datetime(
                    row.changed_at.astimezone(UTC), usegmt=True
                )
                SubElement(props, "Etag").text = row.etag
                if service == "file":
                    SubElement(props, "Quota").text = str(row.properties.get("quota", 5120))
                else:
                    SubElement(props, "LeaseStatus").text = "unlocked"
                    SubElement(props, "LeaseState").text = "available"
                if "metadata" in includes:
                    metadata = SubElement(item, "Metadata")
                    for key, value in row.properties.get("metadata", {}).items():
                        SubElement(metadata, key).text = value
            SubElement(root, "NextMarker").text = (
                rows[maximum - 1].name if len(rows) > maximum else ""
            )
            return xml_response(request, root)
        row = await session.get(
            StorageNamespace, (access.tenant_id, access.account_id, service, name)
        )
        if not row:
            raise StorageError(
                404,
                "ContainerNotFound" if service == "blob" else "ShareNotFound",
                "The resource was not found",
            )
        if request.headers.get("x-ms-lease-id"):
            raise StorageError(
                412, "LeaseNotPresentWithContainerOperation", "No active container lease"
            )
        if request.headers.get("If-Match") not in {None, "*", row.etag}:
            raise StorageError(412, "ConditionNotMet", "The ETag precondition failed")
        if request.headers.get("If-None-Match") in {"*", row.etag}:
            raise StorageError(
                304 if request.method in {"GET", "HEAD"} else 412,
                "ConditionNotMet",
                "The ETag precondition failed",
            )
        if action == "get_acl":
            xml = Element("SignedIdentifiers")
            for policy in row.policies:
                item = SubElement(xml, "SignedIdentifier")
                SubElement(item, "Id").text = policy["id"]
                acl = SubElement(item, "AccessPolicy")
                for field, tag in [
                    ("start", "Start"),
                    ("expiry", "Expiry"),
                    ("permission", "Permission"),
                ]:
                    if policy.get(field):
                        SubElement(acl, tag).text = policy[field]
            return xml_response(request, xml, extra=properties_headers(view(row), service))
        if action in {"set_acl", "metadata"}:
            if action == "set_acl":
                policies = parse_policies(await bounded_body(request), service)
                if service == "blob":
                    arm = await session.get(ArmResource, (access.tenant_id, access.account_id))
                    public = public_access(request, arm)
                    row.properties = dict(row.properties, public_access=public)
                row.policies = policies
            else:
                if await bounded_body(request):
                    raise StorageError(400, "InvalidRequestBody", "Metadata requires an empty body")
                row.properties = dict(row.properties, metadata=metadata_headers(request))
            row.etag = '"' + uuid4().hex + '"'
            row.changed_at = now
            await record(
                session,
                access,
                service,
                name,
                ("SetContainer" if service == "blob" else "SetShare")
                + ("ACL" if action == "set_acl" else "Metadata"),
                now,
            )
            return Response(
                status_code=200, headers=headers(request) | properties_headers(view(row), service)
            )
        if action == "delete":
            await record(
                session,
                access,
                service,
                name,
                "DeleteContainer" if service == "blob" else "DeleteShare",
                now,
            )
            await session.delete(row)
            return Response(status_code=202, headers=headers(request))
        return Response(
            status_code=200, headers=headers(request) | properties_headers(view(row), service)
        )


async def bounded_body(request, limit=32768):
    result = bytearray()
    async for chunk in request.stream():
        result.extend(chunk)
        if len(result) > limit:
            raise StorageError(
                413, "RequestBodyTooLarge", "Storage request body exceeds the local limit"
            )
    return bytes(result)


def metadata_headers(request):
    import re

    result = {}
    size = 0
    for key, value in request.headers.items():
        if not key.startswith("x-ms-meta-"):
            continue
        name = key[10:]
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            or not value.isascii()
            or any(ord(c) < 32 for c in value)
        ):
            raise StorageError(400, "InvalidMetadata", "Metadata names and values are invalid")
        size += len(key) + len(value)
        if size > 8192 or name in result:
            raise StorageError(400, "MetadataTooLarge", "Metadata exceeds the supported limit")
        result[name] = value
    if "x-ms-meta" in request.headers:
        # Original azure-storage-file-share sends its generated map header as
        # well as the documented expanded metadata headers. Accept redundancy
        # only when it exactly matches the applied metadata.
        import ast

        raw = request.headers["x-ms-meta"]
        try:
            if len(raw) > 8192:
                raise ValueError()
            redundant = ast.literal_eval(raw)
            if not isinstance(redundant, dict) or redundant != result:
                raise ValueError()
        except (ValueError, SyntaxError, RecursionError):
            raise StorageError(
                400, "InvalidMetadata", "Conflicting metadata representations"
            ) from None
    return result


def public_access(request, arm):
    value = request.headers.get("x-ms-blob-public-access", "")
    if value not in {"", "blob", "container"}:
        raise StorageError(400, "InvalidHeaderValue", "Invalid public access level")
    if value and not arm.payload["properties"].get("allowBlobPublicAccess"):
        raise StorageError(409, "PublicAccessNotPermitted", "The account disallows public access")
    return value
