"""Registered device-user navigation with delegated-only mutations."""

from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import String, cast, literal, select

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.directory.references import parse_local_reference
from mockazure.identity.models import User
from mockazure.providers.graph.body import invalid, read_json
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.permissions import readable_fields
from mockazure.providers.graph.probes import permitted, principal
from mockazure.providers.graph.security.queries import parse_security_query
from mockazure.security.audit import record_security_audit
from mockazure.security.models import DeviceUser
from mockazure.security.paging import security_collection
from mockazure.security.repository import document, lock_security, missing, public_security_object

READ = OperationRequirement(
    "graph",
    frozenset(
        {"Device.Read.All", "Device.ReadWrite.All", "Directory.Read.All", "Directory.ReadWrite.All"}
    ),
    frozenset({"Device.Read.All", "Directory.Read.All", "Directory.ReadWrite.All"}),
)
WRITE = OperationRequirement(
    "graph",
    frozenset(),
    frozenset({"Directory.AccessAsUser.All"}),
    frozenset({"Intune Administrator", "Windows 365 Administrator", "Global Administrator"}),
)


def router():
    routes = APIRouter(prefix="/v1.0")

    async def handle(request: Request, identifier: str, user_id: str | None = None):
        async with request.app.state.database.sessions.begin() as session:
            actor = await principal(request, session)
            read = request.method == "GET"
            await permitted(actor, READ if read else WRITE, session)
            await lock_security(session, actor.tenant_id)
            device = await public_security_object(
                session,
                actor.tenant_id,
                "device",
                identifier,
                by_appid=request.url.path.split("/")[2].startswith("devices("),
            )
            device_id = UUID(device["id"])
            if read:
                if set(request.query_params) - {"$select", "$top", "$skiptoken"}:
                    raise GraphError(
                        400, "Request_UnsupportedQuery", "Unsupported registeredUsers query"
                    )
                visible = readable_fields(actor, "user")
                payload = document(
                    id=cast(User.id, String),
                    displayName=User.display_name
                    if "displayName" in visible
                    else literal(None, String),
                    userPrincipalName=User.upn
                    if "userPrincipalName" in visible
                    else literal(None, String),
                    **{"@odata.type": "#microsoft.graph.user"},
                )
                source = (
                    select(User.id.label("id"), payload.label("payload"))
                    .join(
                        DeviceUser,
                        (DeviceUser.tenant_id == User.tenant_id) & (DeviceUser.user_id == User.id),
                    )
                    .where(
                        User.tenant_id == actor.tenant_id,
                        User.deleted_at.is_(None),
                        DeviceUser.device_id == device_id,
                    )
                    .subquery()
                )
                query = (
                    None
                    if "$skiptoken" in request.query_params
                    else parse_security_query(request, "registeredUser")
                )
                return await security_collection(
                    request, session, actor, "registeredUser", query, source=source
                )
            if request.query_params:
                invalid("Write query options are unsupported")
            if request.method == "POST":
                body = await read_json(request)
                if set(body) != {"@odata.id"}:
                    invalid("A single @odata.id is required")
                settings = request.app.state.settings
                origins = frozenset(
                    [
                        settings.endpoints.graph,
                        *(p.graph for p in settings.client_profiles.values()),
                    ]
                )
                user = parse_local_reference(body["@odata.id"], origins, actor.tenant_id)
                if urlsplit(body["@odata.id"]).path.split("/")[2] not in {
                    "directoryObjects",
                    "users",
                }:
                    invalid("A registered user reference must identify a user")
            else:
                try:
                    user = UUID(user_id)
                except ValueError:
                    invalid("Invalid user ID")
            target = await session.get(User, user)
            if (
                target is None
                or target.tenant_id != actor.tenant_id
                or target.deleted_at is not None
            ):
                missing()
            edge = await session.get(DeviceUser, (actor.tenant_id, device_id, user))
            adding = request.method == "POST"
            if adding and edge is not None:
                invalid("The registered user reference already exists")
            if not adding and edge is None:
                missing()
            if adding:
                session.add(
                    DeviceUser(tenant_id=actor.tenant_id, device_id=device_id, user_id=user)
                )
            else:
                await session.delete(edge)
            await record_security_audit(
                session,
                actor,
                "Add registered user to device" if adding else "Remove registered user from device",
                {"id": str(device_id), "type": "Device", "displayName": device.get("displayName")},
                {"registeredUsers": [] if adding else [str(user)]},
                {"registeredUsers": [str(user)] if adding else []},
                datetime.now(UTC),
            )
            return Response(status_code=204)

    for prefix in ("/devices/{identifier}", "/devices(deviceId='{identifier}')"):
        routes.add_api_route(prefix + "/registeredUsers", handle, methods=["GET"])
        routes.add_api_route(prefix + "/registeredUsers/$ref", handle, methods=["POST"])
    routes.add_api_route(
        "/devices/{identifier}/registeredUsers/{user_id}/$ref", handle, methods=["DELETE"]
    )
    return routes
