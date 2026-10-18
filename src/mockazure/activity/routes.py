"""Office 365 Management Activity pull API; no outbound webhook delivery."""

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.responses import JSONResponse, Response

from mockazure.activity.access import authorized, invalid, query, require_dlp, subscription
from mockazure.activity.contracts import CONTENT_TYPES, window
from mockazure.activity.models import Content, Cursor, Subscription
from mockazure.activity.subscriptions import change, state, view

router = APIRouter(prefix="/api/v1.0/{tenant}/activity/feed")


def kind_parameter(values):
    kind = values.get("contentType")
    if kind is None:
        invalid("AF20001", "contentType is required")
    if kind not in CONTENT_TYPES:
        invalid("AF20020", "The specified content type is invalid")
    return kind


def origin(request):
    return request.app.state.settings.advertised_endpoints(request.headers.get("host", "")).activity


def content_view(request, tenant, row):
    return {
        "contentType": row.content_type,
        "contentId": row.id,
        "contentUri": f"{origin(request)}/api/v1.0/{tenant}/activity/feed/audit/{row.id}",
        "contentCreated": row.published_at.isoformat(),
        "contentExpiration": (row.published_at + timedelta(days=7)).isoformat(),
    }


async def empty_body(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > 65536:
            from mockazure.activity.errors import ActivityError

            raise ActivityError(413, "AF20002", "Request exceeds the local 64 KiB limit")
        data.extend(chunk)
    if not data:
        return
    if request.headers.get("content-type", "").split(";")[0].lower().strip() != "application/json":
        invalid("AF20002", "Content-Type must be application/json")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, RecursionError):
        invalid("AF20002", "Invalid JSON object")
    if value != {}:
        invalid("AF20021", "Webhook configuration and other request properties are unsupported")


@router.post("/subscriptions/start")
async def start(tenant: str, request: Request):
    values, publisher = query(request, {"contentType"})
    kind = kind_parameter(values)
    await empty_body(request)
    async with authorized(request, tenant, publisher) as (session, actor, now):
        require_dlp(actor, kind)
        return await change(session, actor, kind, True)


@router.post("/subscriptions/stop")
async def stop(tenant: str, request: Request):
    values, publisher = query(request, {"contentType"})
    kind = kind_parameter(values)
    await empty_body(request)
    async with authorized(request, tenant, publisher) as (session, actor, now):
        require_dlp(actor, kind)
        row = await session.get(Subscription, (actor.tenant_id, actor.client_id, kind))
        if row is None:
            invalid("AF20022", "No subscription found for the specified content type")
        await change(session, actor, kind, False)
        return Response(status_code=200)


@router.get("/subscriptions/list")
async def list_subscriptions(tenant: str, request: Request):
    values, publisher = query(request, set())
    async with authorized(request, tenant, publisher) as (session, actor, now):
        rows = await session.scalars(
            select(Subscription)
            .where(
                Subscription.tenant_id == actor.tenant_id, Subscription.client_id == actor.client_id
            )
            .order_by(Subscription.content_type)
        )
        return [
            view(row)
            for row in rows
            if row.content_type != "DLP.All" or "ActivityFeed.ReadDlp" in actor.permissions
        ]


@router.get("/subscriptions/content")
async def list_content(tenant: str, request: Request):
    values, publisher = query(request, {"contentType", "startTime", "endTime", "nextPage"})
    kind = kind_parameter(values)
    async with authorized(request, tenant, publisher) as (session, actor, now):
        sub = await subscription(session, actor, kind)
        current = await state(session, actor.tenant_id, create=False)
        binding = {
            "contentType": kind,
            "publisher": str(publisher),
            "subscription": str(sub.generation),
            "generation": str(current.generation),
            "permissions": sorted(actor.permissions),
        }
        token = values.get("nextPage")
        if token is not None:
            cursor = (
                await session.get(Cursor, hashlib.sha256(token.encode()).hexdigest())
                if len(token) <= 128
                else None
            )
            if (
                cursor is None
                or cursor.tenant_id != actor.tenant_id
                or cursor.client_id != actor.client_id
                or cursor.expires_at <= datetime.now(UTC)
                or cursor.state.get("binding") != binding
            ):
                invalid("AF20031", "Invalid or expired nextPage")
            saved = cursor.state
            if values.get("startTime") != saved["start"] or values.get("endTime") != saved["end"]:
                invalid("AF20031", "Continuation query changed")
            try:
                start, end = window(saved["start"], saved["end"], now)
            except ValueError:
                invalid("AF20055", "The saved publication window has expired")
            upper, after = saved["upper"], saved["after"]
        else:
            try:
                start, end = window(values.get("startTime"), values.get("endTime"), now)
            except ValueError:
                invalid("AF20055", "Invalid publication time window")
            upper, after = current.revision, sub.start_revision
        limit = current.settings.get("page_size", 100)
        rows = list(
            await session.scalars(
                select(Content)
                .where(
                    Content.tenant_id == actor.tenant_id,
                    Content.generation == current.generation,
                    Content.content_type == kind,
                    Content.revision > max(after, sub.start_revision),
                    Content.revision <= upper,
                    Content.published_at >= start,
                    Content.published_at < end,
                    Content.published_at > now - timedelta(days=7),
                )
                .order_by(Content.revision)
                .limit(limit + 1)
            )
        )
        headers = {}
        if len(rows) > limit:
            selected = rows[:limit]
            next_token = secrets.token_urlsafe(32)
            saved = {
                "binding": binding,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "upper": upper,
                "after": selected[-1].revision,
            }
            session.add(
                Cursor(
                    token_hash=hashlib.sha256(next_token.encode()).hexdigest(),
                    tenant_id=actor.tenant_id,
                    client_id=actor.client_id,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                    state=saved,
                )
            )
            parameters = {
                "contentType": kind,
                "startTime": saved["start"],
                "endTime": saved["end"],
                "nextPage": next_token,
            }
            if "PublisherIdentifier" in values:
                parameters["PublisherIdentifier"] = str(publisher)
            headers["NextPageUri"] = (
                f"{origin(request)}/api/v1.0/{actor.tenant_id}/activity/feed/subscriptions/content?{urlencode(parameters)}"
            )
            rows = selected
        return JSONResponse(
            [content_view(request, actor.tenant_id, row) for row in rows], headers=headers
        )


@router.get("/audit/{identifier}")
async def content(tenant: str, identifier: str, request: Request):
    values, publisher = query(request, set())
    async with authorized(request, tenant, publisher) as (session, actor, now):
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            invalid("AF20052", "Invalid content ID")
        row = await session.get(Content, (actor.tenant_id, identifier))
        if row is None:
            invalid("AF20050", "Content does not exist")
        sub = await subscription(session, actor, row.content_type)
        if row.revision <= sub.start_revision:
            invalid("AF20050", "Content is outside this subscription")
        if row.published_at + timedelta(days=7) <= now:
            invalid("AF20051", "Content has expired")
        return row.records
