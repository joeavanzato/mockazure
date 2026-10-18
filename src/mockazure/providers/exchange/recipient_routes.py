"""Observed compiled EXO recipient and permission transports, no client replacements."""

import base64
import re
from urllib.parse import urlencode
from uuid import UUID, uuid5

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.mail.authorization import require_exchange_command, require_mailbox_access
from mockazure.mail.permission_records import read_mailbox_permissions
from mockazure.mail.recipients import RECIPIENT_FIELDS, query_recipients, recipient_view
from mockazure.mail.repository import resolve_mailbox
from mockazure.providers.exchange.probes import permitted
from mockazure.providers.graph.errors import GraphError

router = APIRouter()


def query_options(request, allowed):
    pairs = list(request.query_params.multi_items())
    if len(pairs) != len(dict(pairs)) or set(request.query_params) - allowed:
        raise GraphError(400, "UnsupportedQuery", "Unsupported or duplicate Exchange query option")
    if request.query_params.get("isEncoded", "true").lower() != "true":
        raise GraphError(400, "InvalidIdentity", "Encoded identity is required")


def output(request, tenant, data, resource, *, collection=False, next_link=None):
    origin = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
    body = {
        "@odata.context": f"{origin.exchange}/adminapi/beta/{tenant}/$metadata#{resource}"
        + ("" if collection else "/$entity")
    }
    if collection:
        body["value"] = data
    else:
        body.update(data)
    if next_link:
        body["@odata.nextLink"] = next_link
    return JSONResponse(body, headers={"OData-Version": "4.0"})


def project_recipient(data, fields):
    result = data | {
        "ObjectKey": base64.b64encode(data["ExternalDirectoryObjectId"].encode()).decode()
    }
    if fields:
        names = {name.strip() for name in fields.split(",")}
        if names - RECIPIENT_FIELDS:
            raise GraphError(400, "UnsupportedProperty", "Unsupported recipient projection")
        result = {key: value for key, value in result.items() if key in names or key == "ObjectKey"}
    return result


@router.get("/adminapi/beta/{tenant}/Recipient")
async def recipients(tenant: str, request: Request):
    from mockazure.providers.exchange.routes import decode_identity

    query_options(
        request,
        {
            "ReadIdsAndParamsFromHeaders",
            "RecipientType",
            "RecipientTypeDetails",
            "PropertySet",
            "$select",
            "$filter",
            "$skiptoken",
        },
    )
    query = request.query_params
    if query.get("PropertySet", "Minimum") != "Minimum":
        raise GraphError(
            400, "UnsupportedPropertySet", "Only Minimum and explicit properties are supported"
        )
    values = {}
    if "ReadIdsAndParamsFromHeaders" in query:
        if query["ReadIdsAndParamsFromHeaders"].lower() != "true" or not request.headers.get(
            "XParameter-Identity"
        ):
            raise GraphError(400, "InvalidIdentity", "Encoded identity header required")
        values["Identity"] = decode_identity(request.headers["XParameter-Identity"])
    elif request.headers.get("XParameter-Identity"):
        raise GraphError(400, "InvalidIdentity", "Identity header requires its query flag")
    if any(k.startswith("xparameter-") and k != "xparameter-identity" for k in request.headers):
        raise GraphError(400, "UnsupportedQuery", "Unsupported Exchange parameter header")
    for name in ("RecipientType", "RecipientTypeDetails"):
        if name in query:
            values[name] = query[name].split(",")
    if "$filter" in query:
        values["Filter"] = query["$filter"]
    prefer = request.headers.get("Prefer", "odata.maxpagesize=1000;")
    match = re.fullmatch(r"odata.maxpagesize=(\d+);?", prefer)
    if not match or not 1 <= int(match[1]) <= 1000:
        raise GraphError(400, "UnsupportedPageSize", "Page size must be between 1 and 1000")
    size = int(match[1])
    values["ResultSize"] = str(size + 1)
    try:
        after = UUID(query["$skiptoken"]) if "$skiptoken" in query else None
    except ValueError:
        raise GraphError(400, "InvalidContinuation", "Invalid recipient continuation") from None
    try:
        async with request.app.state.database.sessions() as session:
            actor = await permitted(request, tenant, session)
            data = await query_recipients(
                session, actor, values, "Get-EXORecipient", after=after, odata=True
            )
            next_link = None
            if len(data) > size:
                params = dict(query) | {"$skiptoken": data[size - 1]["ExternalDirectoryObjectId"]}
                origin = request.app.state.settings.advertised_endpoints(
                    request.headers.get("host", "")
                )
                next_link = f"{origin.exchange}/adminapi/beta/{tenant}/Recipient?" + urlencode(
                    params
                )
            return output(
                request,
                tenant,
                [project_recipient(v, query.get("$select")) for v in data[:size]],
                "Recipient",
                collection=True,
                next_link=next_link,
            )
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange recipient scope is required"
        ) from None


@router.get("/adminapi/beta/{tenant}/Recipient('{key}')")
async def recipient(tenant: str, key: str, request: Request):
    from mockazure.providers.exchange.routes import decode_identity

    query_options(request, {"isEncoded", "$select", "$expand"})
    expansion = request.query_params.get("$expand")
    if expansion is not None and expansion != "RecipientPermission":
        raise GraphError(400, "UnsupportedExpansion", "Only RecipientPermission is supported")
    try:
        async with request.app.state.database.sessions() as session:
            actor = await permitted(request, tenant, session)
            operation = "Get-EXORecipientPermission" if expansion else "Get-EXORecipient"
            await require_exchange_command(session, actor, operation)
            box = await resolve_mailbox(session, actor.tenant_id, decode_identity(key))
            await require_mailbox_access(session, actor, box, operation)
            if expansion:
                fields = {
                    v.strip()
                    for v in request.query_params.get(
                        "$select", "RecipientPermission,ExternalDirectoryObjectId"
                    ).split(",")
                }
                if fields - {"RecipientPermission", "ExternalDirectoryObjectId"}:
                    raise GraphError(
                        400, "UnsupportedProperty", "Unsupported permission projection"
                    )
                data = {
                    "ObjectKey": key,
                    "ExternalDirectoryObjectId": str(box.user_id),
                    "RecipientPermission": await read_mailbox_permissions(
                        session, actor, box.user_id, True, operation=operation
                    ),
                }
            else:
                data = project_recipient(
                    await recipient_view(session, box), request.query_params.get("$select")
                )
            return output(request, tenant, data, "Recipient")
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange recipient scope is required"
        ) from None


@router.get("/adminapi/beta/{tenant}/Mailbox('{key}')/MailboxPermission")
async def mailbox_permissions(tenant: str, key: str, request: Request):
    from mockazure.providers.exchange.routes import decode_identity

    query_options(request, {"isEncoded"})
    try:
        async with request.app.state.database.sessions() as session:
            actor = await permitted(request, tenant, session)
            await require_exchange_command(session, actor, "Get-EXOMailboxPermission")
            box = await resolve_mailbox(session, actor.tenant_id, decode_identity(key))
            data = await read_mailbox_permissions(
                session, actor, box.user_id, False, operation="Get-EXOMailboxPermission"
            )
            # This compiled DTO is NOT the flattened PowerShell output shape.
            # Its OData key is PermissionId and ACEs live inside PermissionList.
            grouped = {}
            for row in data:
                principal = row["User"]
                key = principal.casefold()
                if key not in grouped:
                    grouped[key] = {
                        "PermissionId": str(uuid5(box.user_id, "mailbox-permission:" + key)),
                        "MailboxIdentity": box.primary_smtp,
                        "User": principal,
                        "PermissionList": [],
                    }
                grouped[key]["PermissionList"].append(
                    {
                        name: value
                        for name, value in row.items()
                        if name in {"AccessRights", "Deny", "InheritanceType", "IsInherited"}
                    }
                )
            return output(
                request,
                tenant,
                list(grouped.values()),
                "Collection(Exchange.MailboxPermission)",
                collection=True,
            )
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange mailbox scope is required"
        ) from None
