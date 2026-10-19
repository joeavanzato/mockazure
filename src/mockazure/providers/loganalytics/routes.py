"""Dedicated Logs v1 origin with service-shaped query, metadata and legacy batch."""

import json
import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from mockazure.identity.tokens import validate_access_token
from mockazure.monitor.logs import metadata, query_workspace
from mockazure.oauth.errors import OAuthError
from mockazure.providers.arm.authorization import current_principal
from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.loganalytics.errors import LogsError, response
from mockazure.query.types import QueryError


def preference(value):
    if not value:
        return 30
    options = {}
    for part in value.split(","):
        name, sep, argument = part.strip().lower().partition("=")
        if not sep or name in options:
            raise LogsError(400, "BadArgumentError", "Invalid query preference")
        options[name] = argument.strip()
    if set(options) - {"wait", "include-statistics", "include-render", "response-v1"} or any(
        options.get(key, "false") != "false" for key in ("include-statistics", "include-render")
    ):
        raise LogsError(400, "BadArgumentError", "Unsupported query preference")
    if options.get("response-v1", "true") != "true":
        raise LogsError(400, "BadArgumentError", "Only v1 query responses are supported")
    wait = options.get("wait", "30")
    if not re.fullmatch(r"\d+", wait) or not 1 <= int(wait) <= 600:
        raise LogsError(400, "BadArgumentError", "Supported wait preference is 1..600")
    return int(wait)


async def body_json(request):
    parts, size = [], 0
    async for part in request.stream():
        size += len(part)
        if size > 1024 * 1024:
            raise LogsError(413, "RequestTooLarge", "Logs request exceeds 1 MiB")
        parts.append(part)
    return json.loads(b"".join(parts))


def mapped_error(error):
    if isinstance(error, LogsError):
        return error
    if isinstance(error, QueryError) and error.code == "QueryLimitExceeded":
        return LogsError(400, "QueryExecutionError", str(error))
    return LogsError(400, "BadArgumentError", "Invalid or unsupported Logs query")


async def handle(request):
    settings = request.app.state.settings
    headers = {"x-ms-request-id": request.state.request_id}
    try:
        authority = request.headers.getlist("host")
        profile = next(
            (
                p
                for p in (settings.endpoints, *settings.client_profiles.values())
                if len(authority) == 1 and urlsplit(p.logs).netloc.lower() == authority[0].lower()
            ),
            None,
        )
        if profile is None or request.url.scheme != "https":
            raise LogsError(400, "BadArgumentError", "Host or scheme does not match Logs listener")
        now = datetime.now(UTC)
        async with request.app.state.database.sessions.begin() as session:
            scheme, _, token = request.headers.get("authorization", "").partition(" ")
            try:
                if scheme.lower() != "bearer":
                    raise OAuthError("invalid_token", "Bearer token required")
                actor = await validate_access_token(
                    token, "logs", now, session, endpoints=settings.endpoints
                )
            except OAuthError:
                headers["WWW-Authenticate"] = (
                    f'Bearer authorization_uri="{profile.login}/common/oauth2/authorize", resource="https://api.loganalytics.io"'
                )
                raise LogsError(
                    401, "InvalidTokenError", "A valid Logs access token is required"
                ) from None
            try:
                await current_principal(
                    session,
                    actor,
                    audience="https://api.loganalytics.io",
                    delegated_scope="Data.Read",
                )
            except ArmError:
                raise LogsError(
                    403, "InsufficientAccessError", "Current Logs identity is required"
                ) from None
            args = request.query_params
            if len(args) != len(args.multi_items()):
                raise LogsError(400, "BadArgumentError", "Duplicate query arguments")
            if request.url.path == "/v1/$batch" and request.method == "POST":
                if args:
                    raise LogsError(
                        400, "BadArgumentError", "Batch query arguments are unsupported"
                    )
                result = await batch(session, actor, await body_json(request), now)
            else:
                match = re.fullmatch(
                    r"/v1/workspaces/([0-9a-fA-F-]+)/(?P<op>query|metadata)", request.url.path
                )
                if match is None:
                    raise LogsError(404, "PathNotFoundError", "Logs endpoint was not found")
                workspace_id = UUID(match[1])
                if match["op"] == "metadata" and request.method == "GET":
                    if args:
                        raise LogsError(
                            400, "BadArgumentError", "Metadata query arguments are unsupported"
                        )
                    result = await metadata(session, actor, workspace_id)
                    validate_arm_payload("MetadataResults", result, namespace="loganalytics")
                elif match["op"] == "query" and request.method in {"GET", "POST"}:
                    allowed = (
                        {"query", "timespan", "workspaces"}
                        if request.method == "GET"
                        else {"timespan"}
                    )
                    if set(args) - allowed:
                        raise LogsError(400, "BadArgumentError", "Unsupported Logs query option")
                    if request.method == "GET":
                        body = {"query": args.get("query", "")}
                        if "workspaces" in args:
                            body["workspaces"] = args["workspaces"].split(",")
                    else:
                        body = await body_json(request)
                    timeout = preference(request.headers.get("prefer", ""))
                    result = await query_workspace(
                        session,
                        actor,
                        workspace_id,
                        body,
                        now,
                        url_timespan=args.get("timespan"),
                        timeout=timeout,
                    )
                else:
                    raise LogsError(405, "MethodNotAllowed", "Unsupported Logs method")
        return JSONResponse(result, headers=headers)
    except (LogsError, ValueError, TypeError, KeyError, OverflowError) as error:
        return response(mapped_error(error), headers)
    except SQLAlchemyError:
        return response(
            LogsError(503, "ServiceUnavailableError", "Logs database is unavailable"), headers
        )


async def batch(session, actor, body, now):
    validate_arm_payload("BatchRequest", body, namespace="loganalytics")
    requests = body["requests"]
    if (
        set(body) != {"requests"}
        or not 1 <= len(requests) <= 20
        or len({r["id"] for r in requests}) != len(requests)
    ):
        raise LogsError(400, "BadArgumentError", "Batch requires 1..20 distinct request IDs")
    responses = []
    for item in requests:
        try:
            if (
                set(item) - {"id", "headers", "body", "path", "method", "workspace"}
                or item["method"].upper() != "POST"
                or item["path"] != "/query"
            ):
                raise LogsError(400, "BadArgumentError", "Unsupported batch operation")
            headers = {name.lower(): value for name, value in item.get("headers", {}).items()}
            if set(headers) - {"prefer", "content-type"}:
                raise LogsError(400, "BadArgumentError", "Unsupported batch header")
            timeout = preference(headers.get("prefer", ""))
            # Driver cancellation can invalidate an entire connection. Each
            # batch item therefore owns its transaction/connection, not merely
            # a savepoint inside the batch's authentication transaction.
            from sqlalchemy.ext.asyncio import async_sessionmaker

            sessions = async_sessionmaker(session.bind, expire_on_commit=False, info=session.info)
            async with sessions.begin() as item_session:
                result = await query_workspace(
                    item_session, actor, UUID(item["workspace"]), item["body"], now, timeout=timeout
                )
            status = 200
        except (LogsError, ValueError, TypeError, KeyError, OverflowError) as error:
            mapped = mapped_error(error)
            status, result = mapped.status, mapped.body()
        responses.append(
            {
                "id": item["id"],
                "status": status,
                "body": result,
                "headers": {"Content-Type": "application/json"},
            }
        )
    return {"responses": responses}
