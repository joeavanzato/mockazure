from datetime import UTC, datetime

from fastapi import APIRouter, Request

from mockazure.hunting.service import run_hunting
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal
from mockazure.query.types import QueryError

router = APIRouter(prefix="/v1.0/security")


@router.post("/runHuntingQuery")
@router.post("/microsoft.graph.security.runHuntingQuery", include_in_schema=False)
async def query(request: Request):
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        if request.query_params:
            raise GraphError(400, "BadRequest", "Hunting accepts a JSON body without query options")
        body = await read_json(request, limit=128 * 1024)
        try:
            result = await run_hunting(session, actor, body, wall_now=datetime.now(UTC))
        except QueryError as error:
            raise GraphError(400, error.code, str(error)) from None
        except (ValueError, OverflowError, TypeError):
            raise GraphError(400, "BadRequest", "Invalid typed hunting query request") from None
        endpoints = request.app.state.settings.advertised_endpoints(request.headers.get("host", ""))
        return {
            "@odata.context": endpoints.graph
            + "/v1.0/$metadata#microsoft.graph.security.huntingQueryResults",
            **result,
        }
