"""Published Resource Graph 2024-04-01 query envelope."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request

from mockazure.providers.arm.contracts import validate_arm_payload
from mockazure.providers.arm.errors import ArmError
from mockazure.providers.arm.probes import principal
from mockazure.providers.resourcegraph.paging import page_result, page_state
from mockazure.query.compiler import execute_query
from mockazure.query.parser import parse_query
from mockazure.query.types import QueryError, QueryScope
from mockazure.query.wire import cell

router = APIRouter()


@router.post("/providers/Microsoft.ResourceGraph/resources")
async def resources(request: Request):
    pairs = list(request.query_params.multi_items())
    if pairs != [("api-version", "2024-04-01")]:
        raise ArmError(
            400, "InvalidApiVersionParameter", "Resource Graph requires api-version 2024-04-01"
        )
    try:
        raw = await request.body()
        if len(raw) > 256 * 1024:
            raise QueryError("Resource Graph request exceeds 256 KiB")
        import json

        body = json.loads(raw)
        validate_arm_payload("QueryRequest", body, namespace="resourcegraph")
        if set(body) - {"query", "subscriptions", "options"}:
            raise QueryError("Unsupported Resource Graph query option")
        options = body.get("options", {})
        if set(options) - {"$top", "$skip", "$skipToken", "resultFormat", "allowPartialScopes"}:
            raise QueryError("Unsupported Resource Graph execution option")
        if options.get("allowPartialScopes", False):
            raise QueryError("Partial-scope execution is not supported")
        subs = tuple(str(UUID(s)) for s in body.get("subscriptions", []))
        if "subscriptions" in body and not subs:
            raise QueryError("subscriptions cannot be empty")
        if len(subs) > 1000:
            raise QueryError("At most 1000 explicit subscriptions are supported")
        plan = parse_query(body["query"], "resourcegraph")
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            state, instant, predecessor = await page_state(session, actor, body, datetime.now(UTC))
            result = await execute_query(
                session,
                actor,
                QueryScope(actor.tenant_id, subscription_ids=subs),
                plan,
                instant,
            )
            rows, more, token = await page_result(session, state, result, predecessor)
        rows = [
            [cell(value, kind) for value, (_, kind) in zip(row, result.columns, strict=True)]
            for row in rows
        ]
        if options.get("resultFormat", "objectArray") == "table":
            data = {"columns": [{"name": n, "type": t} for n, t in result.columns], "rows": rows}
        else:
            data = [dict(zip([n for n, _ in result.columns], row, strict=True)) for row in rows]
        response = {
            "totalRecords": len(result.rows),
            "count": len(rows),
            "resultTruncated": "true" if more else "false",
            "data": data,
        }
        if token:
            response["$skipToken"] = token
        validate_arm_payload("QueryResponse", response, namespace="resourcegraph")
        return response
    except QueryError as error:
        raise ArmError(400, error.code, str(error)) from None
    except (ValueError, TypeError, KeyError):
        raise ArmError(
            400, "BadRequest", "Request does not match the Resource Graph query contract"
        ) from None
