from fastapi import APIRouter, Request, Response
from sqlalchemy import String, Uuid, and_, case, cast, column, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB
from starlette.responses import JSONResponse

from mockazure.authorization.roles import security_now
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import context_url, principal
from mockazure.providers.graph.security.queries import parse_security_query
from mockazure.providers.graph.security.serialization import serialize_security
from mockazure.security.mutations import uuid_value
from mockazure.security.paging import security_collection
from mockazure.security.pim.contracts import invalid
from mockazure.security.pim.models import PimRequest, PimSchedule
from mockazure.security.pim.policies import (
    assignment_id,
    policy_id,
    policy_rules,
    require_policy_access,
    update_rule,
)
from mockazure.security.pim.requests import (
    cancel_request,
    expire_cancelled_requests,
    require_access,
    submit_request,
)
from mockazure.security.repository import document, lock_security, missing
from mockazure.security.roles import role_ids

router = APIRouter(prefix="/v1.0")


def time_text(value):
    return func.to_char(func.timezone("UTC", value), 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')


def schedule_source(tenant, kind, view, now):
    row = PimSchedule
    if view == "Requests":
        req = PimRequest
        status = case(
            (
                and_(req.payload["status"].as_string() == "Granted", row.starts_at <= now),
                "Provisioned",
            ),
            else_=req.payload["status"].as_string(),
        )
        payload = req.payload.op("||")(
            document(
                status=status,
                completedDateTime=case(
                    (
                        and_(req.payload["status"].as_string() == "Granted", row.starts_at <= now),
                        time_text(row.starts_at),
                    ),
                    else_=req.payload["completedDateTime"].as_string(),
                ),
            )
        )
        return (
            select(req.id.label("id"), payload.label("payload"))
            .outerjoin(row, and_(row.tenant_id == req.tenant_id, row.id == req.schedule_id))
            .where(req.tenant_id == tenant, req.kind == kind)
            .subquery()
        )
    if view == "Instances":
        identifier = cast(func.md5(cast(row.id, String) + ":instance"), Uuid)
        label = "Eligibility" if kind == "eligibility" else "Assignment"
        fields = {
            "id": cast(identifier, String),
            "principalId": cast(row.principal_id, String),
            "roleDefinitionId": row.payload["roleDefinitionId"].as_string(),
            "directoryScopeId": "/",
            "appScopeId": None,
            "memberType": "Direct",
            "startDateTime": time_text(row.starts_at),
            "endDateTime": time_text(func.least(row.ends_at, row.revoked_at)),
            "role" + label + "ScheduleId": cast(row.id, String),
        }
        if kind == "assignment":
            fields.update(
                assignmentType=row.payload["assignmentType"].as_string(),
                roleAssignmentOriginId=cast(row.id, String),
            )
        return (
            select(identifier.label("id"), document(**fields).label("payload"))
            .where(
                row.tenant_id == tenant,
                row.kind == kind,
                row.starts_at <= now,
                (row.revoked_at.is_(None) | (row.revoked_at > row.starts_at)),
            )
            .subquery()
        )
    status = case(
        (row.revoked_at.is_not(None), "Revoked"),
        (row.ends_at <= now, "Expired"),
        (row.starts_at > now, "Granted"),
        else_="Provisioned",
    )
    return (
        select(row.id.label("id"), row.payload.op("||")(document(status=status)).label("payload"))
        .where(row.tenant_id == tenant, row.kind == kind)
        .subquery()
    )


def register(kind, view):
    label = "Eligibility" if kind == "eligibility" else "Assignment"
    resource = "role" + label + "Schedule" + ("s" if not view else view)
    type_name = (
        "unifiedRole"
        + label
        + "Schedule"
        + ("Request" if view == "Requests" else "Instance" if view == "Instances" else "")
    )
    path = "/roleManagement/directory/" + resource

    async def read(request: Request):
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            current = "on" in request.path_params
            if current and (
                request.path_params["on"] != "principal" or actor.token_kind != "delegated"
            ):
                invalid(
                    "This profile supports filterByCurrentUser(on='principal') "
                    "for delegated callers"
                )
            await require_access(session, actor, kind, own=current, write=current)
            if view == "Requests":
                await expire_cancelled_requests(session, actor.tenant_id, security_now())
            source = schedule_source(actor.tenant_id, kind, view, security_now())
            if current:
                source = (
                    select(source)
                    .where(source.c.payload["principalId"].as_string() == str(actor.subject_id))
                    .subquery()
                )
            identifier = request.path_params.get("identifier")
            if identifier:
                if set(request.query_params) - {"$select"}:
                    invalid("Item reads support $select only")
                query = parse_security_query(request, type_name)
                payload = await session.scalar(
                    select(source.c.payload).where(source.c.id == uuid_value(identifier))
                )
                if payload is None:
                    missing()
                return {
                    "@odata.context": context_url(request, resource + "/$entity"),
                    **serialize_security(payload, query),
                }
            query = (
                None
                if "$skiptoken" in request.query_params
                else parse_security_query(request, type_name)
            )
            return await security_collection(
                request, session, actor, type_name, query, source=source
            )

    router.add_api_route(path, read, methods=["GET"])
    if view == "Requests":
        router.add_api_route(path + "/filterByCurrentUser(on='{on}')", read, methods=["GET"])
    router.add_api_route(path + "/{identifier}", read, methods=["GET"])
    if view != "Requests":
        return

    async def create(request: Request):
        if request.query_params:
            invalid("Write query options are unsupported")
        body = await read_json(request)
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            result = await submit_request(session, actor, kind, body, security_now())
            return JSONResponse(result, status_code=201)

    async def cancel(identifier: str, request: Request):
        if request.query_params or await request.body():
            invalid("Cancel accepts no body or query")
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            await cancel_request(session, actor, kind, uuid_value(identifier), security_now())
        return Response(status_code=204)

    router.add_api_route(path, create, methods=["POST"])
    router.add_api_route(path + "/{identifier}/cancel", cancel, methods=["POST"])
    router.add_api_route(path + "/{identifier}/microsoft.graph.cancel", cancel, methods=["POST"])


for _kind in ("eligibility", "assignment"):
    for _view in ("", "Requests", "Instances"):
        register(_kind, _view)


def static_source(values):
    rows = func.jsonb_array_elements(literal(values, JSONB)).table_valued(column("value", JSONB))
    return select(
        rows.c.value["id"].as_string().label("id"), rows.c.value.label("payload")
    ).subquery()


async def policy_read(request: Request):
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        await require_policy_access(session, actor)
        # PIM policy inventory uses the same tenant entitlement gate.
        from mockazure.licensing.service import entitlements

        if (await entitlements(session, actor.tenant_id, None)).tenant_tier != "P2":
            raise GraphError(403, "Authorization_RequestDenied", "PIM requires Entra P2")
        assignments = "roleManagementPolicyAssignments" in request.url.path
        policy = request.path_params.get("policy")
        identifier = request.path_params.get("identifier")
        values = []
        for role in role_ids():
            pid = policy_id(actor.tenant_id, role)
            if policy and policy != pid:
                continue
            if policy:
                values.extend((await policy_rules(session, actor.tenant_id, role)).values())
            elif assignments:
                values.append(
                    {
                        "id": assignment_id(actor.tenant_id, role),
                        "policyId": pid,
                        "roleDefinitionId": role_ids()[role],
                        "scopeId": "/",
                        "scopeType": "DirectoryRole",
                    }
                )
            else:
                values.append(
                    {
                        "id": pid,
                        "displayName": role,
                        "description": "Local tenant-scope privileged access policy",
                        "scopeId": "/",
                        "scopeType": "DirectoryRole",
                        "isOrganizationDefault": False,
                    }
                )
        if policy and not values:
            missing()
        type_name = "unifiedRoleManagementPolicy" + (
            "Rule" if policy else "Assignment" if assignments else ""
        )
        if policy:
            # Mixed derived rule shapes: selection is bounded to their union.
            if request.query_params:
                invalid("Policy rule reads do not support query options in this profile")
            if identifier:
                value = next((v for v in values if v["id"] == identifier), None)
                if value is None:
                    missing()
                return value
            return {"@odata.context": context_url(request, request.url.path[6:]), "value": values}
        if identifier:
            if set(request.query_params) - {"$select"}:
                invalid()
            query = parse_security_query(request, type_name)
            value = next((v for v in values if v["id"] == identifier), None)
            if value is None:
                missing()
            return serialize_security(value, query)
        query = (
            None
            if "$skiptoken" in request.query_params
            else parse_security_query(request, type_name)
        )
        return await security_collection(
            request, session, actor, type_name, query, source=static_source(values)
        )


async def policy_write(policy: str, identifier: str, request: Request):
    if request.query_params:
        invalid()
    body = await read_json(request)
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        await lock_security(session, actor.tenant_id)
        role = next((r for r in role_ids() if policy_id(actor.tenant_id, r) == policy), None)
        if role is None:
            missing()
        from mockazure.licensing.service import entitlements

        if (await entitlements(session, actor.tenant_id, None)).tenant_tier != "P2":
            raise GraphError(403, "Authorization_RequestDenied", "PIM requires Entra P2")
        await update_rule(session, actor, role, identifier, body, security_now())
    return Response(status_code=204)


for _resource in ("roleManagementPolicies", "roleManagementPolicyAssignments"):
    router.add_api_route("/policies/" + _resource, policy_read, methods=["GET"])
    router.add_api_route("/policies/" + _resource + "/{identifier}", policy_read, methods=["GET"])
router.add_api_route(
    "/policies/roleManagementPolicies/{policy}/rules", policy_read, methods=["GET"]
)
router.add_api_route(
    "/policies/roleManagementPolicies/{policy}/rules/{identifier}", policy_read, methods=["GET"]
)
router.add_api_route(
    "/policies/roleManagementPolicies/{policy}/rules/{identifier}", policy_write, methods=["PATCH"]
)
