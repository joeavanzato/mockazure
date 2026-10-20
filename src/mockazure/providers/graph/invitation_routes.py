from datetime import UTC, datetime

from fastapi import APIRouter, Request

from mockazure.identity.invitations import create_invitation
from mockazure.providers.graph.body import invalid, read_json
from mockazure.providers.graph.probes import context_url, principal

router = APIRouter(prefix="/v1.0")


@router.post("/invitations", status_code=201)
async def invite(request: Request):
    if request.query_params:
        invalid("Invitation creation accepts no query options")
    settings = request.app.state.settings
    origins = {
        settings.endpoints.browser_login,
        *(p.browser_login for p in settings.client_profiles.values()),
    }
    origin = settings.advertised_endpoints(request.headers.get("host", "")).browser_login
    body = await read_json(request, limit=16384)
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        result = await create_invitation(
            session, actor, body, datetime.now(UTC), origins=origins, origin=origin
        )
    return {"@odata.context": context_url(request, "invitations/$entity"), **result}
