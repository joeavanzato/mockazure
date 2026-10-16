"""Cross-replica limits; independent transactions survive denied provider requests."""

import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal


async def consume_budget(session, tenant_id, client_id, resource, now, limit, window_seconds):
    if (
        resource
        not in {"users", "groups", "signIn", "directoryAudit", "riskDetection", "riskyUser"}
        or limit < 1
        or not 1 <= window_seconds <= 3600
    ):
        raise ValueError("Invalid directory budget")
    start = datetime.fromtimestamp(
        math.floor(now.timestamp() / window_seconds) * window_seconds, UTC
    )
    row = (
        await session.execute(
            text("""
INSERT INTO graph_request_budgets(tenant_id,client_id,resource,window_start,used)
VALUES (:tenant,:client,:resource,:start,1)
ON CONFLICT (tenant_id,client_id,resource) DO UPDATE SET
 used = CASE WHEN graph_request_budgets.window_start < :start THEN 1
             ELSE LEAST(graph_request_budgets.used+1,:limit+1) END,
 window_start = GREATEST(graph_request_budgets.window_start,:start)
RETURNING used,window_start
"""),
            dict(tenant=tenant_id, client=client_id, resource=resource, start=start, limit=limit),
        )
    ).one()
    if row.used <= limit:
        return None
    return max(
        1, math.ceil((row.window_start + timedelta(seconds=window_seconds) - now).total_seconds())
    )


async def enforce_directory_budget(request):
    parts = request.url.path.split("/")
    if len(parts) < 3 or parts[1] not in {"v1.0", "beta"}:
        return
    from mockazure.providers.graph.evidence.contracts import PATHS

    resource = next(
        (family for family, path in PATHS.items() if "/".join(parts[2:4]) == path), None
    )
    evidence = resource is not None
    if not evidence:
        if parts[2] not in {"users", "groups"}:
            return
        resource = parts[2]
    now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
    async with request.app.state.database.sessions.begin() as session:
        context = await principal(request, session)
        settings = request.app.state.settings
        retry = await consume_budget(
            session,
            context.tenant_id,
            context.client_id,
            resource,
            now,
            600 if evidence else settings.graph_directory_request_limit,
            60 if evidence else settings.graph_directory_window_seconds,
        )
        if retry is None and request.method == "GET":
            retry = await session.scalar(
                text("""
DELETE FROM graph_read_faults
WHERE tenant_id=:tenant AND client_id=:client AND resource=:resource
RETURNING retry_after
"""),
                dict(tenant=context.tenant_id, client=context.client_id, resource=resource),
            )
    # The counter and one-shot fault commit even though this request is denied.
    if retry is not None:
        error = GraphError(429, "TooManyRequests", "Graph request limit reached; retry later")
        error.headers["Retry-After"] = str(retry)
        raise error
