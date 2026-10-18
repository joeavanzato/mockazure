"""Graph v1.0 site discovery, list evidence and selected application grants."""

from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import and_, func, select

from mockazure.admin.errors import AdminError
from mockazure.providers.graph.body import read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.probes import principal
from mockazure.workloads.models import WorkloadGrant as G
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.queries import (
    collection_response,
    invalid,
    options,
    page_state,
    project,
    snapshots,
)
from mockazure.workloads.repository import current_record, scope_row, visible
from mockazure.workloads.resource_access import (
    authorize_operation,
    authorize_site,
    remove_grant,
    set_grant,
)

router = APIRouter(prefix="/v1.0")


def missing():
    raise GraphError(404, "itemNotFound", "Resource was not found")


async def resolve_site(session, actor, path):
    if "\\" in path or any(part in {".", ".."} for part in path.split("/")):
        invalid("Invalid site path")
    if ":/" in path:
        hostname, rest = path.split(":/", 1)
        site_path, separator, tail = rest.partition(":/")
        site_path = site_path.rstrip(":")
        url = "https://" + hostname.lower() + "/" + site_path
        rows = list(
            await session.scalars(
                select(V)
                .where(
                    V.tenant_id == actor.tenant_id,
                    V.workload == "sharepoint",
                    V.kind == "site",
                    V.valid_to.is_(None),
                    V.deleted.is_(False),
                    func.lower(func.rtrim(V.payload["webUrl"].as_string(), "/"))
                    == url.rstrip("/").lower(),
                )
                .limit(2)
            )
        )
        if len(rows) != 1:
            missing()
        return rows[0], tail.split("/") if separator else []
    identifier, _, tail = path.partition("/")
    if identifier == "root":
        rows = list(
            await session.scalars(
                select(V)
                .where(
                    V.tenant_id == actor.tenant_id,
                    V.workload == "sharepoint",
                    V.kind == "site",
                    V.valid_to.is_(None),
                    V.deleted.is_(False),
                    V.payload.has_key("root"),
                )
                .limit(2)
            )
        )
        if len(rows) != 1:
            missing()
        site = rows[0]
    else:
        site = await current_record(session, actor.tenant_id, "sharepoint", "site", identifier)
        if site is None:
            missing()
    return site, tail.split("/") if tail else []


async def linked_record(session, actor, site, source, relation, identifier=None):
    query = (
        select(V)
        .join(
            E,
            and_(
                E.tenant_id == V.tenant_id,
                E.workload == V.workload,
                E.scope == V.scope,
                E.target_kind == V.kind,
                E.target_id == V.id,
            ),
        )
        .where(
            V.tenant_id == actor.tenant_id,
            V.workload == "sharepoint",
            V.scope == site.id,
            V.valid_to.is_(None),
            V.deleted.is_(False),
            E.source_kind == source.kind,
            E.source_id == source.id,
            E.relation == relation,
            E.valid_to.is_(None),
        )
    )
    if identifier is not None:
        query = query.where(V.id == identifier)
    rows = list(await session.scalars(query.limit(2)))
    if len(rows) != 1:
        missing()
    return rows[0]


async def render_list_item(session, actor, row, opts, revision):
    value = project(row, opts["select"])
    if opts["expand"]:
        fields = await session.scalar(
            select(V)
            .join(
                E,
                and_(
                    E.tenant_id == V.tenant_id,
                    E.workload == V.workload,
                    E.scope == V.scope,
                    E.target_id == V.id,
                    E.target_kind == V.kind,
                ),
            )
            .where(
                V.tenant_id == actor.tenant_id,
                V.workload == "sharepoint",
                V.scope == row.scope,
                V.kind == "fieldValueSet",
                visible(V, revision),
                V.deleted.is_(False),
                E.source_kind == "listItem",
                E.source_id == row.id,
                E.relation == "fields",
                visible(E, revision),
            )
        )
        if fields:
            selected = (
                opts["expand"][15:-1].split(",")
                if opts["expand"].startswith("fields($select=")
                else None
            )
            value["fields"] = project(fields, selected)
    return value


def grant_view(row):
    identity = {"application": {"id": str(row.principal_id)}}
    if row.display_name:
        identity["application"]["displayName"] = row.display_name
    return {"id": str(row.id), "roles": [row.role], "grantedToIdentitiesV2": [identity]}


@router.get("/sites")
async def search_sites(request: Request):
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        await authorize_operation(session, actor, "search")
        state, token = await page_state(request, session, actor, "site", "search", search=True)
        query = select(V).where(
            V.tenant_id == actor.tenant_id,
            V.workload == "sharepoint",
            V.kind == "site",
            V.deleted.is_(False),
            snapshots(state),
        )
        term = state["options"]["search"]
        if term != "*":
            query = query.where(func.lower(V.name).contains(term.lower(), autoescape=True))
        return await collection_response(request, session, actor, query, "sites", state, token)


@router.api_route("/sites/{site_path:path}", methods=["GET", "POST", "DELETE"])
async def site_endpoint(site_path: str, request: Request):
    async with request.app.state.database.sessions.begin() as session:
        actor = await principal(request, session)
        # Require an operation-level permission before looking up identifiers.
        operation = "permissions" if "/permissions" in site_path else "site"
        await authorize_operation(session, actor, operation)
        site, parts = await resolve_site(session, actor, site_path)
        await authorize_site(session, actor, site.id, operation)
        if parts and parts[0] == "permissions":
            if len(parts) > 2:
                missing()
            if request.method == "POST" and len(parts) == 1:
                if request.query_params:
                    invalid()
                body = await read_json(request, limit=16384)
                body.pop("@odata.type", None)
                if set(body) != {"roles", "grantedToIdentities"} or body["roles"] not in (
                    ["read"],
                    ["write"],
                    ["owner"],
                    ["fullcontrol"],
                ):
                    invalid("Provide one supported role and one application")
                identities = body["grantedToIdentities"]
                if (
                    not isinstance(identities, list)
                    or len(identities) != 1
                    or not isinstance(identities[0], dict)
                    or set(identities[0]) != {"application"}
                ):
                    invalid("Only application grants are supported")
                application = identities[0]["application"]
                if not isinstance(application, dict) or set(application) - {"id", "displayName"}:
                    invalid("Invalid application identity")
                try:
                    app_id = UUID(application["id"])
                    display = application.get("displayName")
                    if display is not None and (not isinstance(display, str) or len(display) > 256):
                        raise ValueError()
                    row = await set_grant(
                        session,
                        actor.tenant_id,
                        site.id,
                        "application",
                        app_id,
                        body["roles"][0],
                        display,
                    )
                except (ValueError, KeyError, TypeError, AdminError):
                    invalid("Application grant requires a current local principal")
                # Native audit publication is added by the site permission adapter.
                from fastapi.responses import JSONResponse

                return JSONResponse(grant_view(row), status_code=201)
            if request.method == "DELETE" and len(parts) == 2:
                if request.query_params:
                    invalid()
                try:
                    await remove_grant(session, actor.tenant_id, UUID(parts[1]), scope=site.id)
                except (ValueError, AdminError):
                    missing()
                return Response(status_code=204)
            if request.method != "GET":
                raise GraphError(405, "notAllowed", "Unsupported site permission operation")
            opts = options(request, "permission", item=True)
            grants = list(
                await session.scalars(
                    select(G)
                    .where(
                        G.tenant_id == actor.tenant_id,
                        G.workload == "sharepoint",
                        G.scope == site.id,
                        G.principal_kind == "application",
                    )
                    .order_by(G.id)
                )
            )
            values = [grant_view(row) for row in grants]
            if len(parts) == 2:
                value = next((row for row in values if row["id"] == parts[1]), None)
                if value is None:
                    missing()
                return {
                    k: v
                    for k, v in value.items()
                    if opts["select"] is None or k in opts["select"] or k == "id"
                }
            return {"value": values}
        if request.method != "GET":
            raise GraphError(405, "notAllowed", "Only site permission mutations are implemented")
        if not parts:
            return project(site, options(request, "site", item=True)["select"])
        source = site
        relation = parts.pop(0)
        grammar = {
            ("site", "drives"): "drive",
            ("site", "lists"): "list",
            ("list", "items"): "listItem",
            ("listItem", "fields"): "fieldValueSet",
        }
        while True:
            kind = grammar.get((source.kind, relation))
            if kind is None:
                raise GraphError(
                    501, "MockAzureOperationNotImplemented", "Site relationship is not implemented"
                )
            if relation == "fields":
                if parts:
                    missing()
                row = await linked_record(session, actor, site, source, relation)
                return project(row, options(request, kind, item=True)["select"])
            if not parts:
                state, token = await page_state(request, session, actor, kind, "site", site.id)
                revision = state["scopes"][site.id]["upper"]
                query = (
                    select(V)
                    .join(
                        E,
                        and_(
                            E.tenant_id == V.tenant_id,
                            E.workload == V.workload,
                            E.scope == V.scope,
                            E.target_kind == V.kind,
                            E.target_id == V.id,
                        ),
                    )
                    .where(
                        V.tenant_id == actor.tenant_id,
                        V.workload == "sharepoint",
                        V.scope == site.id,
                        V.kind == kind,
                        V.deleted.is_(False),
                        visible(V, revision),
                        E.source_kind == source.kind,
                        E.source_id == source.id,
                        E.relation == relation,
                        visible(E, revision),
                    )
                )

                async def render(row, opts):
                    return (
                        await render_list_item(session, actor, row, opts, revision)
                        if kind == "listItem"
                        else project(row, opts["select"])
                    )

                return await collection_response(
                    request, session, actor, query, kind, state, token, render
                )
            source = await linked_record(session, actor, site, source, relation, parts.pop(0))
            if not parts:
                opts = options(request, source.kind, item=True)
                state = await scope_row(session, actor.tenant_id, "sharepoint", site.id)
                return (
                    await render_list_item(session, actor, source, opts, state.revision)
                    if source.kind == "listItem"
                    else project(source, opts["select"])
                )
            relation = parts.pop(0)
