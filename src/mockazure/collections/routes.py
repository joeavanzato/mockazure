from datetime import UTC, datetime

from fastapi import APIRouter, Request

from mockazure.collections.contracts import TYPES
from mockazure.collections.query import parse
from mockazure.collections.repository import read_records
from mockazure.providers.graph.probes import principal
from mockazure.providers.graph.query.contracts import unsupported

router = APIRouter(prefix="/v1.0/security")


def register(family):
    async def handle(request: Request, record_id: str | None = None):
        pairs = list(request.query_params.multi_items())
        token = None
        values = {}
        if any(k == "$skiptoken" for k, _ in pairs):
            if record_id is not None or len(pairs) != 1:
                unsupported("Use continuation without other options")
            token = pairs[0][1]
        else:
            values = parse(pairs, family, item=record_id is not None)
        origin = request.app.state.settings.advertised_endpoints(
            request.headers.get("host", "")
        ).graph
        now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
        async with request.app.state.database.sessions.begin() as session:
            context = await principal(request, session)
            return await read_records(
                session, context, family, values, token, origin, now, record_id
            )

    router.add_api_route("/" + family, handle, methods=["GET"])
    router.add_api_route("/" + family + "/{record_id}", handle, methods=["GET"])


for family in TYPES:
    register(family)
