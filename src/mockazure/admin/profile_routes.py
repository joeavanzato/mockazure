"""Authenticated immutable profile bundle authoring."""

import json
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.routing import APIRoute
from sqlalchemy import select

from mockazure.admin.errors import AdminError
from mockazure.population.models import BundleHead
from mockazure.population.repository import load_bundle_version, save_bundle
from mockazure.profiles.loader import load_bundle
from mockazure.providers.graph.body import read_json


class ProfileRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handled(request):
            try:
                return await original(request)
            except (ValueError, TypeError, KeyError):
                raise AdminError(
                    422, "InvalidProfileInput", "Profile or run input is invalid"
                ) from None

        return handled


router = APIRouter(prefix="/_mock/profiles", route_class=ProfileRoute)


def parse_bundle(body):
    if set(body) != {"documents"} or not isinstance(body["documents"], dict):
        raise ValueError("Expected a document bundle")
    return load_bundle(
        {
            key: (value if isinstance(value, str) else json.dumps(value)).encode()
            for key, value in body["documents"].items()
        }
    )


@router.get("")
async def list_profiles(
    request: Request, after: UUID | None = None, limit: int = Query(100, ge=1, le=500)
):
    async with request.app.state.database.sessions() as session:
        query = select(BundleHead).order_by(BundleHead.id).limit(limit + 1)
        if after:
            query = query.where(BundleHead.id > after)
        rows = list(await session.scalars(query))
    return {
        "builtin": {"id": "builtin", "revision": 1},
        "items": [{"id": r.id, "revision": r.revision} for r in rows[:limit]],
        "next_after": rows[limit - 1].id if len(rows) > limit else None,
    }


@router.post("", status_code=201)
async def import_bundle(request: Request):
    value = parse_bundle(await read_json(request, limit=16 * 1024 * 1024))
    return await save_bundle(request.app.state.database, value)


@router.get("/{identifier}")
async def get_bundle(identifier: str, request: Request, revision: int | None = Query(None, ge=1)):
    bundle, version = await load_bundle_version(request.app.state.database, identifier, revision)
    return {
        "id": identifier,
        "revision": version,
        "documents": {
            key: value.model_dump(mode="json", exclude_unset=True)
            for key, value in bundle.documents.items()
        },
    }


@router.put("/{identifier}")
async def update_bundle(
    identifier: UUID, request: Request, expected_revision: int = Query(..., ge=1)
):
    value = parse_bundle(await read_json(request, limit=16 * 1024 * 1024))
    return await save_bundle(
        request.app.state.database, value, bundle_id=identifier, expected_revision=expected_revision
    )
