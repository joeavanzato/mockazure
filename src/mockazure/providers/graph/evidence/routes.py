from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.evidence.contracts import PATHS, evidence_contract
from mockazure.providers.graph.evidence.paging import EvidenceRead, read_evidence
from mockazure.providers.graph.evidence.query import parse_evidence_query
from mockazure.providers.graph.probes import principal
from mockazure.providers.graph.query.contracts import unsupported


def router_for(version):
    router = APIRouter(prefix="/" + version)
    if version == "v1.0":
        from mockazure.providers.graph.evidence.risk_actions import register_risk_actions

        register_risk_actions(router)

    def register(family, path):
        async def handle(request: Request, record_id: str | None = None):
            target = None
            if record_id is not None:
                try:
                    target = UUID(record_id)
                except ValueError:
                    # Authorize first even for unresolvable target spelling.
                    target = UUID(int=0)
            pairs = list(request.query_params.multi_items())
            token = None
            if any(k == "$skiptoken" for k, _ in pairs):
                if record_id is not None or len(pairs) != 1:
                    unsupported("A continuation must be used without other query options")
                token = pairs[0][1]
                if not token:
                    unsupported("Empty continuation")
                query = None
            else:
                query = parse_evidence_query(
                    pairs, evidence_contract(version, family), item=record_id is not None
                )
            now = getattr(request.app.state, "clock", lambda: datetime.now(UTC))()
            profile = request.app.state.settings.advertised_endpoints(
                request.headers.get("host", "")
            )
            async with request.app.state.database.sessions.begin() as session:
                context = await principal(request, session)
                page = await read_evidence(
                    session,
                    EvidenceRead(
                        context, version, family, query, token, profile.graph, now, target
                    ),
                )
            if (
                family == "signIn"
                and "include-unknown-enum-members" not in request.headers.get("prefer", "").lower()
            ):
                for value in page.value:
                    for policy in value.get("appliedConditionalAccessPolicies", []):
                        if policy.get("result", "").startswith("reportOnly"):
                            policy["result"] = "unknownFutureValue"
            if record_id is not None:
                if not page.value:
                    raise GraphError(404, "Request_ResourceNotFound", "Evidence resource not found")
                return page.value[0]
            result = {
                "@odata.context": f"{profile.graph}/{version}/$metadata#{path}",
                "value": page.value,
            }
            if page.next_link:
                result["@odata.nextLink"] = page.next_link
            return result

        router.add_api_route("/" + path, handle, methods=["GET"])
        router.add_api_route("/" + path + "/{record_id}", handle, methods=["GET"])

    for family, path in PATHS.items():
        register(family, path)
    return router
