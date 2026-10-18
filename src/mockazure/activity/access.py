from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from mockazure.activity.errors import ActivityError
from mockazure.activity.models import ActivityState, Quota, Subscription
from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity
from mockazure.evidence.clock import evidence_now
from mockazure.identity.tokens import validate_access_token
from mockazure.oauth.errors import OAuthError


def invalid(code, message):
    raise ActivityError(400, code, message)


def query(request, allowed):
    values = dict(request.query_params)
    if len(values) != len(request.query_params.multi_items()) or values.keys() - allowed - {
        "PublisherIdentifier"
    }:
        invalid("AF20002", "Unsupported or duplicate query parameter")
    publisher = values.get("PublisherIdentifier")
    try:
        publisher = UUID(publisher) if publisher is not None else UUID(int=0)
    except ValueError:
        invalid("AF20002", "PublisherIdentifier must be a tenant GUID")
    return values, publisher


def require_dlp(actor, kind):
    if kind == "DLP.All" and "ActivityFeed.ReadDlp" not in actor.permissions:
        raise ActivityError(403, "AF10001", "ActivityFeed.ReadDlp is required for DLP content")


async def subscription(session, actor, kind):
    row = await session.scalar(
        select(Subscription)
        .where(
            Subscription.tenant_id == actor.tenant_id,
            Subscription.client_id == actor.client_id,
            Subscription.content_type == kind,
        )
        .with_for_update(read=True)
    )
    if row is None:
        invalid("AF20022", "No subscription found for the specified content type")
    if not row.enabled:
        invalid("AF20023", "The subscription is disabled")
    require_dlp(actor, kind)
    return row


async def quota(session, tenant, publisher, now):
    current = await session.get(ActivityState, tenant)
    config = current.settings if current else {}
    limit, seconds = config.get("request_limit", 2000), config.get("window_seconds", 60)
    await session.execute(
        insert(Quota)
        .values(tenant_id=tenant, publisher_id=publisher, starts_at=now, requests=0)
        .on_conflict_do_nothing()
    )
    row = await session.scalar(
        select(Quota)
        .where(Quota.tenant_id == tenant, Quota.publisher_id == publisher)
        .with_for_update()
    )
    if row.starts_at + timedelta(seconds=seconds) <= now:
        row.starts_at, row.requests = now, 0
    row.requests += 1
    return (
        max(1, int((row.starts_at + timedelta(seconds=seconds) - now).total_seconds()) + 1)
        if row.requests > limit
        else None
    )


@asynccontextmanager
async def authorized(request, tenant, publisher):
    try:
        tenant = UUID(tenant)
    except ValueError:
        invalid("AF20013", "Tenant ID must be a GUID")
    async with request.app.state.database.sessions.begin() as session:
        values = request.headers.getlist("authorization")
        scheme, _, raw = values[0].partition(" ") if len(values) == 1 else ("", "", "")
        if scheme.lower() != "bearer" or not raw:
            raise ActivityError(
                401,
                "AF10001",
                "A Management Activity bearer token is required",
                {"WWW-Authenticate": "Bearer"},
            )
        try:
            actor = await validate_access_token(
                raw,
                "activity",
                datetime.now(UTC),
                session,
                endpoints=request.app.state.settings.endpoints,
            )
        except OAuthError:
            raise ActivityError(
                401,
                "AF10001",
                "Invalid Management Activity access token",
                {"WWW-Authenticate": "Bearer"},
            ) from None
        if actor.tenant_id != tenant:
            invalid("AF20010", "Token and URL tenant IDs must match")
        try:
            await require_current_identity(actor, session)
        except AuthorizationDenied:
            raise ActivityError(
                403, "AF10001", "Current application identity is required"
            ) from None
        if actor.token_kind != "application" or "ActivityFeed.Read" not in actor.permissions:
            raise ActivityError(403, "AF10001", "Application ActivityFeed.Read is required")
        now = await evidence_now(session, tenant, datetime.now(UTC))
        retry = await quota(session, tenant, publisher, datetime.now(UTC))
    if retry:
        raise ActivityError(
            429, "AF429", "Publisher request quota exceeded", {"Retry-After": str(retry)}
        )
    async with request.app.state.database.sessions.begin() as session:
        yield session, actor, now
