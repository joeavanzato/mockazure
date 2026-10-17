"""ARM-specific bindings over the shared opaque continuation store."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from mockazure.providers.arm.contracts import operation_contract
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.query import invalid, parse_filter
from mockazure.providers.graph.continuations import (
    CursorState,
    binding_for,
    issue_cursor,
    resolve_cursor,
)
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.query.ast import ExpandSpec, QuerySpec


def parameters(request, operation, *, collection=False, inventory=False):
    args = request.query_params
    try:
        operation_contract(operation, args.get("api-version"))
    except ValueError:
        raise ArmError(400, "InvalidApiVersionParameter", "Unsupported ARM API version") from None
    allowed = {"api-version"}
    if collection:
        allowed |= {"$skiptoken"}
    if inventory:
        allowed |= {"$top", "$filter", "$expand"}
    if len(args.multi_items()) != len(args) or set(args) - allowed:
        invalid("Duplicate or unsupported query parameters")
    if "$skiptoken" in args and set(args) != {"api-version", "$skiptoken"}:
        invalid("Continuation options cannot be changed")
    return args["api-version"]


async def read_page(request, session, actor, version, revision, *, inventory=False):
    now = datetime.now(UTC)
    binding = await binding_for(session, actor, version, "arm:" + request.url.path.lower())
    token = request.query_params.get("$skiptoken")
    if token:
        try:
            state = await resolve_cursor(session, token, binding, now)
        except GraphError as error:
            raise ArmError(
                error.status, "InvalidSkipToken", "ARM continuation is unavailable"
            ) from None
        if state.upper_revision != revision:
            invalid("Inventory changed; restart collection")
        return state
    args = request.query_params
    top = 100
    if inventory:
        try:
            raw = args.get("$top", "100")
            if not raw.isascii() or not raw.isdigit() or not 1 <= int(raw) <= 1000:
                invalid("top must be an integer in 1..1000")
            top = min(int(raw), 100)
        except ValueError:
            invalid("Invalid top")
    expand = tuple(args.get("$expand", "").split(",")) if "$expand" in args else ()
    if len(expand) != len(set(expand)) or set(expand) - {
        "createdTime",
        "changedTime",
        "provisioningState",
    }:
        invalid("Unsupported expand")
    query = QuerySpec(
        filter=parse_filter(args["$filter"]) if "$filter" in args else None,
        top=top,
        expand=tuple(ExpandSpec(n) for n in expand),
    )
    return CursorState(binding, query, None, revision, None, "page", now + timedelta(hours=1))


async def next_link(request, session, state, last, *, token_key="$skiptoken"):
    from dataclasses import replace

    token = await issue_cursor(
        session,
        replace(state, last_key=(str(last),)),
        predecessor=request.query_params.get(token_key),
    )
    profile = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
    return (
        profile.arm
        + request.url.path
        + "?"
        + urlencode({"api-version": state.binding.version, token_key: token})
    )
