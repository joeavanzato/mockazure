from dataclasses import replace
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from mockazure.providers.graph.probes import principal
from mockazure.providers.graph.query.contracts import unsupported
from mockazure.providers.graph.query.parser import parse_query
from mockazure.providers.graph.security.queries import contract_for
from mockazure.security.paging import security_collection
from mockazure.security.registration import (
    normalize_filter,
    registration_source,
    require_report_read,
)
from mockazure.security.repository import missing

KIND = "userRegistrationDetails"
PATH = "/reports/authenticationMethods/userRegistrationDetails"


def router_for(version):
    router = APIRouter(prefix="/" + version)

    @router.get(PATH)
    @router.get(PATH + "/{identifier}")
    async def read(request: Request, identifier: str | None = None):
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            await require_report_read(session, actor)
            source = registration_source(actor.tenant_id)
            if identifier is not None:
                if request.query_params:
                    unsupported("Registration item query options are unsupported")
                try:
                    key = UUID(identifier)
                except ValueError:
                    missing()
                payload = await session.scalar(select(source.c.payload).where(source.c.id == key))
                if payload is None:
                    missing()
                return payload
            items = list(request.query_params.multi_items())
            if any(key not in {"$filter", "$skiptoken"} for key, _ in items):
                unsupported("Registration report supports filter and continuation only")
            query = None
            if "$skiptoken" not in request.query_params:
                query = parse_query(items, contract_for(KIND, version), None)
                query = replace(query, filter=normalize_filter(query.filter))
            return await security_collection(request, session, actor, KIND, query, source=source)

    return router
