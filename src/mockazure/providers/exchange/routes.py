"""OData transport consumed by the pinned original Exchange compiled cmdlets."""

import base64
import binascii

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.mail.authorization import require_mailbox_access
from mockazure.mail.mailboxes import cas_view, mailbox_statistics, mailbox_view
from mockazure.mail.models import MailboxFolder
from mockazure.mail.repository import resolve_mailbox
from mockazure.providers.exchange.probes import permitted
from mockazure.providers.graph.errors import GraphError

router = APIRouter()


def decode_identity(value):
    try:
        if len(value) > 4096:
            raise ValueError()
        identity = base64.b64decode(value, validate=True).decode("utf-8")
        if not identity or len(identity) > 1024:
            raise ValueError()
        return identity
    except (ValueError, UnicodeError, binascii.Error):
        raise GraphError(400, "InvalidIdentity", "Invalid Exchange encoded identity") from None


@router.get("/adminapi/beta/{tenant}/Mailbox('{key}')")
async def mailbox(tenant: str, key: str, request: Request):
    if set(request.query_params) - {"$select", "isEncoded"}:
        raise GraphError(400, "UnsupportedQuery", "Unsupported Exchange query option")
    if request.query_params.get("isEncoded", "true").lower() != "true":
        raise GraphError(400, "InvalidIdentity", "Encoded identity is required")
    try:
        async with request.app.state.database.sessions() as session:
            actor = await permitted(request, tenant, session)
            box = await resolve_mailbox(session, actor.tenant_id, decode_identity(key))
            await require_mailbox_access(session, actor, box, "Get-EXOMailbox")
            data = await mailbox_view(session, box)
            data.pop("EffectiveForwardingAddress")
            data.update(
                ObjectKey=key,
                Id=str(box.user_id),
                Guid=str(box.user_id),
                RecipientType="UserMailbox",
            )
            fields = request.query_params.get("$select")
            if fields:
                names = set(fields.split(","))
                if names - data.keys():
                    raise GraphError(400, "UnsupportedProperty", "Unsupported mailbox projection")
                data = {
                    name: value
                    for name, value in data.items()
                    if name in names or name == "ObjectKey"
                }
            origin = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            )
            return JSONResponse(
                {
                    "@odata.context": (
                        f"{origin.exchange}/adminapi/beta/{tenant}/$metadata#Mailbox/$entity"
                    ),
                    **data,
                },
                headers={"OData-Version": "4.0"},
            )
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange command permission required"
        ) from None


@router.get("/adminapi/beta/{tenant}/Mailbox('{key}')/Exchange.GetMailboxStatistics()")
async def statistics(tenant: str, key: str, request: Request):
    if set(request.query_params) - {"isEncoded", "Archive"}:
        raise GraphError(400, "UnsupportedQuery", "Unsupported Exchange statistics query")
    archive = request.query_params.get("Archive", "false").lower()
    if archive not in {"true", "false"}:
        raise GraphError(400, "InvalidParameter", "Archive requires a boolean")
    try:
        async with request.app.state.database.sessions() as session:
            actor = await permitted(request, tenant, session)
            box = await resolve_mailbox(session, actor.tenant_id, decode_identity(key))
            await require_mailbox_access(session, actor, box, "Get-EXOMailboxStatistics")
            data = await mailbox_statistics(session, box, archive=archive == "true")
            data["TotalItemSize"] = data.pop("TotalItemSizeBytes")
            origin = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            )
            return JSONResponse(
                {
                    "@odata.context": (
                        f"{origin.exchange}/adminapi/beta/{tenant}/$metadata"
                        "#Collection(Exchange.MailboxStatistics)"
                    ),
                    "value": [data],
                },
                headers={"OData-Version": "4.0"},
            )
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange command permission required"
        ) from None


@router.get("/adminapi/beta/{tenant}/CasMailbox('{key}')")
async def cas_mailbox(tenant: str, key: str, request: Request):
    if set(request.query_params) - {"isEncoded", "$select"}:
        raise GraphError(400, "UnsupportedQuery", "Unsupported Exchange CAS query")
    try:
        async with request.app.state.database.sessions() as session:
            actor = await permitted(request, tenant, session)
            box = await resolve_mailbox(session, actor.tenant_id, decode_identity(key))
            await require_mailbox_access(session, actor, box, "Get-EXOCasMailbox")
            mailbox_data = await mailbox_view(session, box)
            data = await cas_view(session, box)
            data.pop("UserPrincipalName", None)
            data.update(
                ObjectKey=key, DisplayName=mailbox_data["DisplayName"], Name=mailbox_data["Name"]
            )
            fields = request.query_params.get("$select")
            if fields:
                names = set(fields.split(","))
                if names - data.keys():
                    raise GraphError(400, "UnsupportedProperty", "Unsupported CAS projection")
                data = {
                    name: value
                    for name, value in data.items()
                    if name in names or name == "ObjectKey"
                }
            origin = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            )
            return JSONResponse(
                {
                    "@odata.context": (
                        f"{origin.exchange}/adminapi/beta/{tenant}/$metadata#CasMailbox/$entity"
                    ),
                    **data,
                },
                headers={"OData-Version": "4.0"},
            )
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange command permission required"
        ) from None


@router.get(
    "/adminapi/beta/{tenant}/Mailbox('{key}')/MailboxFolder/"
    "Exchange.GetMailboxFolderStatistics(folderscope=Exchange.ElcFolderType'{scope}')"
)
async def folder_statistics(tenant: str, key: str, scope: str, request: Request):
    if set(request.query_params) - {"isEncoded"} or scope != "All":
        raise GraphError(400, "UnsupportedQuery", "Unsupported Exchange folder scope")
    try:
        async with request.app.state.database.sessions() as session:
            actor = await permitted(request, tenant, session)
            box = await resolve_mailbox(session, actor.tenant_id, decode_identity(key))
            await require_mailbox_access(session, actor, box, "Get-EXOMailboxFolderStatistics")
            folders = list(
                await session.scalars(
                    select(MailboxFolder)
                    .where(
                        MailboxFolder.tenant_id == actor.tenant_id,
                        MailboxFolder.user_id == box.user_id,
                        MailboxFolder.archive.is_(False),
                    )
                    .order_by(MailboxFolder.path, MailboxFolder.id)
                )
            )
            data = [
                {
                    "Name": folder.name,
                    "FolderPath": folder.path,
                    "FolderId": folder.id,
                    "Identity": box.alias + folder.path.replace("/", "\\"),
                    "ItemsInFolder": folder.item_count,
                    "FolderSize": folder.size_bytes,
                    "ItemsInFolderAndSubfolders": sum(
                        row.item_count
                        for row in folders
                        if row.id == folder.id or row.path.startswith(folder.path.rstrip("/") + "/")
                    ),
                    "FolderAndSubfolderSize": sum(
                        row.size_bytes
                        for row in folders
                        if row.id == folder.id or row.path.startswith(folder.path.rstrip("/") + "/")
                    ),
                    "FolderType": "Inbox" if folder.id == "inbox" else "User Created",
                    "ContentMailboxGuid": str(box.exchange_guid),
                }
                for folder in folders
            ]
            origin = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            )
            return JSONResponse(
                {
                    "@odata.context": (
                        f"{origin.exchange}/adminapi/beta/{tenant}/$metadata"
                        "#Collection(Exchange.MailboxFolderStatistics)"
                    ),
                    "value": data,
                },
                headers={"OData-Version": "4.0"},
            )
    except AuthorizationDenied:
        raise GraphError(
            403, "Authorization_RequestDenied", "Exchange command permission required"
        ) from None
