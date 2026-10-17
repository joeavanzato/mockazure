"""Graph inbox rule routes backed by canonical mailbox state."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from mockazure.authorization.evaluator import AuthorizationDenied
from mockazure.mail.authorization import require_mailbox_access
from mockazure.mail.models import MailRule
from mockazure.mail.repository import resolve_mailbox
from mockazure.mail.rule_projection import rule_query, rule_view, selected
from mockazure.mail.rules import mutate_rule, rule_page
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.continuations import (
    CursorState,
    binding_for,
    issue_cursor,
    resolve_cursor,
    state_link,
)
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url, principal


def router_for(version):
    router = APIRouter(prefix="/" + version)

    def register(self_path, item, method):
        async def handle(request: Request):
            try:
                async with request.app.state.database.sessions.begin() as session:
                    actor = await principal(request, session)
                    if self_path and actor.token_kind != "delegated":
                        raise GraphError(400, "BadRequest", "/me requires delegated authentication")
                    identity = (
                        str(actor.subject_id) if self_path else request.path_params["user_id"]
                    )
                    mailbox = await resolve_mailbox(session, actor.tenant_id, identity)
                    operation = {
                        "POST": "create",
                        "PATCH": "update",
                        "DELETE": "delete",
                        "GET": "get" if item else "list",
                    }[method]
                    await require_mailbox_access(session, actor, mailbox, "rules." + operation)
                    identifier = request.path_params.get("rule_id")
                    now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
                    if method != "GET":
                        if request.query_params:
                            raise GraphError(
                                400,
                                "Request_UnsupportedQuery",
                                "Write query options are unsupported",
                            )
                        body = await read_json(request) if method != "DELETE" else {}
                        value = await mutate_rule(
                            session, actor, mailbox, operation, identifier, body, now
                        )
                        if method == "DELETE":
                            return Response(status_code=204)
                        from fastapi.responses import JSONResponse

                        return JSONResponse(value, status_code=201 if method == "POST" else 200)
                    pairs = list(request.query_params.multi_items())
                    if item:
                        query = rule_query(pairs, item=True)
                        row = await session.scalar(
                            select(MailRule).where(
                                MailRule.tenant_id == actor.tenant_id,
                                MailRule.user_id == mailbox.user_id,
                                MailRule.id == identifier,
                                MailRule.family == "inbox",
                                MailRule.hidden.is_(False),
                            )
                        )
                        if row is None:
                            raise GraphError(404, "ErrorItemNotFound", "Rule was not found")
                        return selected(rule_view(row), query.select)
                    # Serialize snapshot capture and cursor issuance with mutation/cleanup.
                    await session.refresh(mailbox, with_for_update=True)
                    resource = f"users/{mailbox.user_id}/mailFolders/inbox/messageRules"
                    binding = await binding_for(session, actor, version, resource)
                    token, cursor = None, None
                    if any(key == "$skiptoken" for key, _ in pairs):
                        if len(pairs) != 1:
                            raise GraphError(
                                400,
                                "Request_UnsupportedQuery",
                                "Use continuation without other options",
                            )
                        token = pairs[0][1]
                        cursor = await resolve_cursor(session, token, binding, now)
                        query = cursor.query
                    else:
                        query = rule_query(pairs)
                    page = await rule_page(session, actor, mailbox, query, cursor)
                    result = {
                        "@odata.context": context_url(request, resource),
                        "value": page["value"],
                    }
                    if page["last_key"]:
                        state = (
                            replace(cursor, last_key=page["last_key"])
                            if cursor
                            else CursorState(
                                binding,
                                query,
                                None,
                                page["revision"],
                                page["last_key"],
                                "page",
                                now + timedelta(hours=1),
                            )
                        )
                        handle = await issue_cursor(session, state, predecessor=token)
                        result["@odata.nextLink"] = state_link(request, version, resource, handle)
                    return result
            except AuthorizationDenied:
                raise GraphError(
                    403, "ErrorAccessDenied", "Mailbox operation is not permitted"
                ) from None

        path = "/me" if self_path else "/users/{user_id}"
        path += "/mailFolders/inbox/messageRules" + ("/{rule_id}" if item else "")
        router.add_api_route(path, handle, methods=[method])

    for self_path in (False, True):
        for item, methods in ((False, ("GET", "POST")), (True, ("GET", "PATCH", "DELETE"))):
            for method in methods:
                register(self_path, item, method)
    return router
