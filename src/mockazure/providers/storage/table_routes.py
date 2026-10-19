"""Azure Table JSON/OData and atomic multipart changesets on the local data listener."""

import json
import re
from datetime import UTC, datetime
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from urllib.parse import parse_qsl, quote, unquote, urlsplit
from uuid import uuid4

from sqlalchemy import select
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response

from mockazure.identity.keys import read_wrapping_key
from mockazure.providers.storage.errors import StorageError, headers
from mockazure.providers.storage.routes import bounded_body
from mockazure.storage.authorization import authorize_storage
from mockazure.storage.lifecycle import lock_accounts
from mockazure.storage.models import StorageAccount
from mockazure.storage.table_query import check_key, invalid, property_name
from mockazure.storage.tables import (
    create_table,
    delete_table,
    entity_view,
    find_entity,
    find_table,
    mutate_entity,
    query_table,
    table_name,
    transact_entities,
)

ENTITY = re.compile(
    r"([A-Za-z][A-Za-z0-9]{2,62})\(PartitionKey='((?:[^']|'')*)',RowKey='((?:[^']|'')*)'\)"
)
TABLE = re.compile(r"Tables\('([A-Za-z][A-Za-z0-9]{2,62})'\)")
SAS = {
    "sv",
    "ss",
    "srt",
    "sp",
    "st",
    "se",
    "sip",
    "spr",
    "sig",
    "si",
    "tn",
    "spk",
    "srk",
    "epk",
    "erk",
}
QUERY = {
    "$filter",
    "$select",
    "$top",
    "NextTableName",
    "NextPartitionKey",
    "NextRowKey",
    "$format",
    "timeout",
}


def parse_path(path, method, body=None):
    if path in {"Tables", "Tables()"}:
        if method == "GET":
            return "list", "", None, None
        if method == "POST":
            if not isinstance(body, dict) or set(body) != {"TableName"}:
                invalid("Create Table requires TableName")
            table_name(body["TableName"])
            return "create", body["TableName"], None, None
    match = TABLE.fullmatch(path)
    if match and method == "DELETE":
        return "delete", match[1], None, None
    match = ENTITY.fullmatch(path)
    if match:
        name, p, r = match.groups()
        p = p.replace("''", "'")
        r = r.replace("''", "'")
        check_key(p)
        check_key(r)
        action = {
            "GET": "read_entity",
            "PUT": "replace_entity",
            "PATCH": "merge_entity",
            "MERGE": "merge_entity",
            "DELETE": "delete_entity",
        }.get(method)
        if action:
            return action, name, p, r
    name = path[:-2] if path.endswith("()") else path
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,62}", name) and name.lower() != "tables":
        if method == "GET":
            return "query_entities", name, None, None
        if method == "POST" and path == name:
            if not isinstance(body, dict) or "PartitionKey" not in body or "RowKey" not in body:
                invalid("Insert Entity requires both keys")
            return "insert_entity", name, check_key(body["PartitionKey"]), check_key(body["RowKey"])
    raise StorageError(
        501, "MockAzureOperationNotImplemented", "This Table operation is not implemented"
    )


def json_body(raw):
    def pairs(values):
        result = {}
        for k, v in values:
            if k in result:
                invalid("Duplicate JSON property")
            result[k] = v
        return result

    try:
        value = json.loads(
            raw, object_pairs_hook=pairs, parse_constant=lambda _: invalid("Nonfinite JSON number")
        )
        if not isinstance(value, dict):
            invalid("Expected JSON object")
        return value
    except (ValueError, UnicodeError):
        invalid("Malformed JSON")


def representation(request):
    accept = request.query_params.get("$format", request.headers.get("accept", "application/json"))
    return representation_mode(accept)


def representation_mode(accept):
    if accept == "json":
        accept = "application/json"
    if accept == "*/*":
        accept = "application/json"
    if not accept.lower().startswith("application/json"):
        raise StorageError(415, "JsonFormatNotSupported", "This version requires JSON")
    match = re.search(r"odata=([a-z]+)", accept)
    mode = match[1] if match else "minimalmetadata"
    if mode not in {"minimalmetadata", "fullmetadata", "nometadata"}:
        invalid("Unknown JSON metadata mode")
    return mode


def entity_path(name, p, r):
    return (
        name
        + "(PartitionKey='"
        + quote(p.replace("'", "''"), safe="'")
        + "',RowKey='"
        + quote(r.replace("'", "''"), safe="'")
        + "')"
    )


def metadata(body, mode, base, name, *, collection=False):
    if mode == "nometadata":
        return {
            k: v
            for k, v in body.items()
            if not k.startswith("odata.") and not k.endswith("@odata.type")
        }
    if collection:
        return {"odata.metadata": base + "$metadata#" + (name or "Tables"), "value": body}
    result = {"odata.metadata": base + "$metadata#" + (name or "Tables") + "/@Element"} | body
    if mode == "fullmetadata" and "PartitionKey" in body and "RowKey" in body:
        path = entity_path(name, body["PartitionKey"], body["RowKey"])
        result.update(
            {
                "odata.id": base + path,
                "odata.editLink": path,
                "odata.type": urlsplit(base).path.strip("/") + "." + name,
            }
        )
    return result


def output(request, body=None, status=200, extra=None, mode="minimalmetadata"):
    head = (
        headers(request)
        | {
            "x-ms-version": request.headers.get("x-ms-version", "2019-02-02"),
            "DataServiceVersion": "3.0;",
        }
        | (extra or {})
    )
    if status == 204:
        return Response(status_code=status, headers=head)
    return JSONResponse(
        body, status_code=status, headers=head, media_type="application/json;odata=" + mode
    )


def sas_bounds(request, access, partition=None, row=None):
    if access.mode != "service_sas":
        return {}
    args = request.query_params
    bounds = {k: args[k] for k in ("spk", "srk", "epk", "erk") if k in args}
    if partition is not None:
        if (
            "spk" in bounds
            and (
                partition < bounds["spk"]
                or (partition == bounds["spk"] and "srk" in bounds and row < bounds["srk"])
            )
        ) or (
            "epk" in bounds
            and (
                partition > bounds["epk"]
                or (partition == bounds["epk"] and "erk" in bounds and row > bounds["erk"])
            )
        ):
            raise StorageError(
                403, "AuthorizationPermissionMismatch", "Entity is outside signed key range"
            )
    return bounds


class SubrequestAuthority:
    """Sign the unchanged outer HTTP request; evaluate the embedded conditional mode."""

    def __init__(self, outer, etag):
        self.outer = outer
        values = {k: v for k, v in outer.headers.items() if k.lower() != "if-match"}
        if etag is not None:
            values["if-match"] = etag
        self.headers = Headers(values)

    def __getattr__(self, name):
        return getattr(self.outer, name)


def parse_batch(request, raw, account):
    if len(raw) > 4194304:
        raise StorageError(413, "RequestBodyTooLarge", "Table batch exceeds 4 MiB")
    content_type = request.headers.get("content-type", "")
    if "\r" in content_type or "\n" in content_type:
        invalid()
    message = BytesParser(policy=default).parsebytes(
        ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + raw
    )
    if (
        message.defects
        or message.get_content_type() != "multipart/mixed"
        or not message.is_multipart()
    ):
        invalid("Expected multipart batch")
    parts = list(message.iter_parts())
    if (
        len(parts) != 1
        or parts[0].get_content_type() != "multipart/mixed"
        or not parts[0].is_multipart()
    ):
        invalid("Exactly one mutation changeset is supported")
    operations = []
    table = None
    for part in parts[0].iter_parts():
        if len(operations) >= 100:
            invalid("Batch exceeds 100 operations")
        if (
            part.defects
            or part.get_content_type() != "application/http"
            or part.get("Content-Transfer-Encoding", "binary").lower() != "binary"
        ):
            invalid("Invalid changeset part")
        data = part.get_payload(decode=True)
        if not isinstance(data, bytes):
            invalid()
        first, separator, rest = data.partition(b"\r\n")
        if not separator:
            invalid("Invalid embedded HTTP request")
        try:
            method, url, protocol = first.decode("ascii").split(" ")
        except (ValueError, UnicodeError):
            invalid("Invalid embedded request line")
        parsed = urlsplit(url)
        if (
            protocol != "HTTP/1.1"
            or parsed.fragment
            or parsed.scheme != request.url.scheme
            or parsed.netloc.lower() != request.headers["host"].lower()
            or not parsed.path.startswith("/" + account + "/")
        ):
            invalid("Embedded request must target this local account")
        try:
            query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            invalid("Invalid embedded query")
        if len(query) > 1 or any(k != "$format" for k, _ in query):
            invalid("Unsupported embedded query option")
        raw_headers, sep, raw_body = rest.partition(b"\r\n\r\n")
        if not sep:
            invalid("Invalid embedded HTTP headers")
        head = BytesParser(policy=default).parsebytes(raw_headers + b"\r\n\r\n")
        if (
            head.defects
            or any(len(head.get_all(k, [])) > 1 for k in head.keys())
            or head.get("Authorization")
            or head.get("Transfer-Encoding")
        ):
            invalid("Invalid embedded headers")
        mode = representation_mode(
            dict(query).get("$format", head.get("Accept", "application/json"))
        )
        body = json_body(raw_body) if raw_body.strip() else {}
        if body and not head.get("Content-Type", "").startswith("application/json"):
            invalid("Expected JSON entity")
        action, name, p, r = parse_path(unquote(parsed.path[len(account) + 2 :]), method, body)
        if action not in {"insert_entity", "replace_entity", "merge_entity", "delete_entity"}:
            invalid("Only entity mutations are allowed in a changeset")
        if table is not None and table.lower() != name.lower():
            invalid("Batch requires one table")
        table = name
        operations.append(
            {
                "action": action,
                "operation": action.removesuffix("_entity"),
                "partition": p,
                "row": r,
                "body": body,
                "etag": head.get("If-Match"),
                "content_id": part.get("Content-ID", head.get("Content-ID")),
                "prefer": head.get("Prefer", ""),
                "mode": mode,
                "table": name,
                "base": parsed.scheme + "://" + parsed.netloc + "/" + account + "/",
            }
        )
    if not operations:
        invalid("Empty changeset")
    return table, operations


def batch_response(request, results, operations, error=None):
    boundary = "batchresponse_" + uuid4().hex
    change = "changesetresponse_" + uuid4().hex
    lines = ["--" + boundary, "Content-Type: multipart/mixed; boundary=" + change, ""]
    if error is not None:
        index = getattr(error, "batch_index", 0)
        results = [
            {
                "status": error.status,
                "body": {
                    "odata.error": {
                        "code": error.code,
                        "message": {"lang": "en-US", "value": str(index) + ":" + error.message},
                    }
                },
                "etag": None,
            }
        ]
        operations = [operations[index] if index < len(operations) else {"content_id": None}]
    for result, op in zip(results, operations, strict=True):
        status = result["status"]
        if error is None and op.get("prefer") == "return-no-content":
            status = 204
        mode = op.get("mode", "minimalmetadata")
        lines += [
            "--" + change,
            "Content-Type: application/http",
            "Content-Transfer-Encoding: binary",
            *(["Content-ID: " + op["content_id"]] if op.get("content_id") is not None else []),
            "",
            f"HTTP/1.1 {status} {HTTPStatus(status).phrase}",
            "Content-Type: application/json;odata=" + mode,
        ]
        if result.get("etag"):
            lines += ["ETag: " + result["etag"]]
        body = result["body"]
        if body and error is None:
            body = metadata(body, mode, op["base"], op["table"])
        lines += ["", json.dumps(body) if status != 204 else ""]
    lines += ["--" + change + "--", "--" + boundary + "--", ""]
    return Response(
        "\r\n".join(lines),
        status_code=202,
        headers=headers(request) | {"x-ms-version": "2019-02-02"},
        media_type="multipart/mixed; boundary=" + boundary,
    )


async def dispatch_table(request):
    settings = request.app.state.settings
    profile = settings.advertised_endpoints(request.headers.get("host", ""))
    if urlsplit(profile.table).netloc.lower() != request.headers.get("host", "").lower():
        raise StorageError(400, "InvalidUri", "Host does not match Table listener")
    args = request.query_params
    if len(args) != len(args.multi_items()) or set(args) - QUERY - SAS:
        invalid("Unknown or duplicate Table query option")
    version = request.headers.get("x-ms-version", args.get("sv"))
    if version not in {"2019-02-02", "2017-07-29"}:
        invalid("Unsupported Table request version", "InvalidHeaderValue")
    if any(
        h in request.headers
        for h in ("if-none-match", "if-modified-since", "if-unmodified-since", "x-http-method")
    ):
        invalid("Unsupported Table conditional or method header")
    mode = representation(request)
    parts = request.scope["path"].lstrip("/").split("/", 1)
    if len(parts) != 2:
        invalid("Table account path required")
    account, path = parts
    raw = await bounded_body(request, 4194304 if path == "$batch" else 1048576)
    body = {}
    if path != "$batch" and raw:
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise StorageError(415, "JsonFormatNotSupported", "JSON Content-Type required")
        body = json_body(raw)
    action, name, p, r = (
        parse_path(path, request.method, body) if path != "$batch" else ("batch", "", None, None)
    )
    if version == "2017-07-29" and action not in {"create", "list", "delete"}:
        invalid("Legacy Table profile supports namespace operations only", "InvalidHeaderValue")
    if (
        action != "batch"
        and action not in {"list", "query_entities", "read_entity"}
        and set(args) & QUERY - {"timeout", "$format"}
    ):
        invalid("Query option not supported on this mutation")
    allowed_query = {
        "list": {"$filter", "$select", "$top", "NextTableName", "$format", "timeout"},
        "query_entities": {
            "$filter",
            "$select",
            "$top",
            "NextPartitionKey",
            "NextRowKey",
            "$format",
            "timeout",
        },
        "read_entity": {"$select", "$format", "timeout"},
    }.get(action, {"timeout", "$format"})
    if set(args) & QUERY - allowed_query:
        invalid("Query option is not supported for this Table operation")
    if "$select" in args:
        selected = args["$select"].split(",")
        if len(set(selected)) != len(selected):
            invalid("Duplicate projection")
        for name_part in selected:
            property_name(name_part)
    if action == "batch" and request.method != "POST":
        invalid("Batch requires POST")
    session = request.app.state.database.sessions()
    try:
        session.info["storage_cipher"] = read_wrapping_key(settings.data_dir)
        owner = await session.scalar(
            select(StorageAccount.tenant_id).where(StorageAccount.name == account)
        )
        if owner is None:
            raise StorageError(404, "ResourceNotFound", "Storage account was not found")
        await lock_accounts(session, owner)
        now = datetime.now(UTC)
        if action == "batch":
            operations = []
            try:
                name, operations = parse_batch(request, raw, account)
                for op in operations:
                    access = await authorize_storage(
                        session,
                        SubrequestAuthority(request, op["etag"]),
                        "table",
                        op["action"],
                        name + "/entity",
                        now,
                    )
                    sas_bounds(request, access, op["partition"], op["row"])
                results = await transact_entities(session, access, name, operations, now)
                response = batch_response(request, results, operations)
            except StorageError as error:
                # Transaction errors are nested responses; outer authorization failures remain 403.
                if error.status == 403:
                    raise
                response = batch_response(request, [], operations, error)
        else:
            access = await authorize_storage(
                session,
                request,
                "table",
                action,
                name
                + ("/entity" if action.endswith("_entity") or action == "query_entities" else ""),
                now,
            )
            bounds = sas_bounds(request, access, p, r)
            base = (
                str(request.url).split("?")[0].split("/" + account + "/")[0] + "/" + account + "/"
            )
            if action == "create":
                row = await create_table(session, access, name, now)
                status = 204 if request.headers.get("Prefer") == "return-no-content" else 201
                response = output(
                    request,
                    metadata({"TableName": row.name}, mode, base, ""),
                    status,
                    {"Location": base + "Tables('" + row.name + "')"},
                    mode,
                )
            elif action == "delete":
                await delete_table(session, access, name, now)
                response = output(request, status=204)
            elif action in {"list", "query_entities"}:
                values, extra = await query_table(session, access, name, args, now, mode, bounds)
                payload = (
                    {"value": values}
                    if mode == "nometadata"
                    else metadata(values, mode, base, name, collection=True)
                )
                response = output(request, payload, extra=extra, mode=mode)
            elif action == "read_entity":
                table = await find_table(session, access, name)
                row = await find_entity(session, access, table, p, r)
                if row is None:
                    raise StorageError(
                        404, "ResourceNotFound", "The specified entity does not exist"
                    )
                projection = args.get("$select")
                projection = projection.split(",") if projection else None
                payload = metadata(entity_view(row, mode, projection), mode, base, table.name)
                response = output(request, payload, extra={"ETag": row.etag}, mode=mode)
            else:
                result = await mutate_entity(
                    session,
                    access,
                    name,
                    p,
                    r,
                    action.removesuffix("_entity"),
                    body,
                    request.headers.get("If-Match"),
                    now,
                    mode=mode,
                )
                status = result["status"]
                if status == 201 and request.headers.get("Prefer") == "return-no-content":
                    status = 204
                extra = {"ETag": result["etag"]} if result["etag"] else {}
                if action == "insert_entity":
                    extra["Location"] = base + entity_path(name, p, r)
                response = output(
                    request,
                    metadata(result["body"], mode, base, name) if result["body"] else None,
                    status,
                    extra,
                    mode,
                )
        await session.commit()
        return response
    except BaseException:
        await session.rollback()
        raise
    finally:
        await session.close()
