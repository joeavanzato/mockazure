"""Sourced Graph deleted-item variants with bound, current-availability paging."""

import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from mockazure.directory.types import USER_FIELDS
from mockazure.identity.deleted import (
    KINDS,
    authorize_deleted,
    available,
    deleted_item,
    deleted_view,
    purge_object,
    restore_object,
)
from mockazure.identity.deleted_models import DeletedObject
from mockazure.providers.graph.body import invalid, read_json
from mockazure.providers.graph.probes import context_url, principal
from mockazure.providers.graph.security.queries import FIELDS
from mockazure.security.models import SecuritySnapshot
from mockazure.security.paging import bad_cursor, binding, token_for

router = APIRouter(prefix="/v1.0/directory/deletedItems")


def projection(kind, params):
    if not set(params) <= {"$select", "$top"} or len(params.multi_items()) != len(params):
        invalid("Deleted-item collections support only $select, $top and bound continuations")
    fields = params.get("$select")
    allowed = (USER_FIELDS if kind == "user" else FIELDS[kind]) | {"deletedDateTime"}
    fields = fields.split(",") if fields else None
    if fields is not None and (not set(fields) <= allowed or len(set(fields)) != len(fields)):
        invalid("Unsupported deleted-object selection")
    try:
        top = int(params.get("$top", "100"))
        if not 1 <= top <= 999:
            raise ValueError()
    except ValueError:
        invalid("Deleted-item page size must be 1..999")
    return {"fields": fields, "top": top}


def select_view(value, fields):
    return (
        value
        if fields is None
        else {"@odata.type": value["@odata.type"], **{key: value.get(key) for key in fields}}
    )


@router.get("")
async def untyped():
    invalid("A microsoft.graph type cast is required for deleted-item lists")


@router.get("/microsoft.graph.{kind}")
async def collection(kind: str, request: Request):
    if kind not in KINDS:
        invalid("Deleted-object type is outside the supported recovery profile")
    now = datetime.now(UTC)
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        await authorize_deleted(session, actor, kind)
        token = request.query_params.get("$skiptoken")
        position = 0
        if token:
            if (
                len(request.query_params.multi_items()) != 1
                or re.fullmatch(r"[0-9a-f]{32}\.[0-9]{1,39}\.[0-9a-f]{64}", token) is None
            ):
                bad_cursor()
            sid, offset, _ = token.split(".")
            snapshot = await session.get(SecuritySnapshot, (actor.tenant_id, UUID(sid)))
            position = int(offset)
            if (
                position >= 2**128
                or snapshot is None
                or snapshot.expires_at <= now
                or snapshot.binding != binding(request, actor, "deleted:" + kind)
                or not hmac.compare_digest(token, token_for(snapshot, position))
            ):
                bad_cursor()
            query = snapshot.query
        else:
            query = {**projection(kind, request.query_params), "cutoff": now.isoformat()}
            snapshot = SecuritySnapshot(
                tenant_id=actor.tenant_id,
                binding=binding(request, actor, "deleted:" + kind),
                query=query,
                signing_secret=secrets.token_hex(32),
                expires_at=now + timedelta(hours=1),
            )
            session.add(snapshot)
            await session.flush()
        rows = list(
            await session.scalars(
                select(DeletedObject)
                .where(
                    *available(actor.tenant_id, now),
                    DeletedObject.kind == kind,
                    DeletedObject.deleted_at <= datetime.fromisoformat(query["cutoff"]),
                    DeletedObject.object_id > UUID(int=position),
                )
                .order_by(DeletedObject.object_id)
                .limit(query["top"] + 1)
            )
        )
        result = {
            "@odata.context": context_url(request, "directory/deletedItems"),
            "value": [
                select_view(deleted_view(row), query["fields"]) for row in rows[: query["top"]]
            ],
        }
        if len(rows) > query["top"]:
            origin = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            ).graph
            result["@odata.nextLink"] = (
                origin
                + request.url.path
                + "?"
                + urlencode(
                    {
                        "$skiptoken": token_for(snapshot, rows[query["top"] - 1].object_id.int),
                    }
                )
            )
        return result


def identifier(value):
    try:
        return UUID(value)
    except ValueError:
        invalid("Deleted-object ID must be a UUID")


@router.get("/{object_id}")
async def item(object_id: str, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        value = await deleted_item(session, actor, identifier(object_id))
        if "$top" in request.query_params:
            invalid("Item reads do not accept $top")
        kind = value["@odata.type"].split(".")[-1]
        fields = projection(kind, request.query_params)["fields"]
        return {
            "@odata.context": context_url(request, "directoryObjects/$entity"),
            **select_view(value, fields),
        }


@router.post("/{object_id}/restore")
@router.post("/{object_id}/microsoft.graph.restore")
async def restore(object_id: str, request: Request):
    if request.query_params:
        invalid("Restore accepts no query options")
    body = await read_json(request) if await request.body() else {}
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        result = await restore_object(
            session, actor, identifier(object_id), body, datetime.now(UTC)
        )
        return {"@odata.context": context_url(request, "directoryObjects/$entity"), **result}


@router.delete("/{object_id}")
async def purge(object_id: str, request: Request):
    if request.query_params or await request.body():
        invalid("Permanent deletion accepts no query options or body")
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        await purge_object(session, actor, identifier(object_id), datetime.now(UTC))
    return Response(status_code=204)
