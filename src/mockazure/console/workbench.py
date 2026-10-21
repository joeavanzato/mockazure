"""Curated console adapters over existing public APIs; no caller-selected origins."""

from dataclasses import replace
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field

from mockazure.admin.errors import AdminError
from mockazure.population.actions import Action, PublicActions
from mockazure.population.catalog import get_adapter, list_families
from mockazure.population.types import AdapterContext, Input, Inspection, Mutation, ResourceQuery
from mockazure.providers.graph.body import read_json

router = APIRouter(prefix="/_mock/console")


class DirectoryActions(PublicActions):
    def describe(self):
        from mockazure.directory.mutations import GROUP_UPDATE, USER_UPDATE

        descriptor = super().describe()
        fields = USER_UPDATE if descriptor.key.endswith("users") else GROUP_UPDATE
        for operation in descriptor.operations:
            if operation.key == "update":
                operation.input_schema["properties"]["body"] = {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        key: {"type": "boolean" if key == "accountEnabled" else "string"}
                        for key in sorted(fields)
                    },
                }
        descriptor.limitations = self.base.describe().limitations + [
            "Directory PATCH retains omitted fields; it is not record replacement.",
            "X-MockAzure-If-Version is a local console concurrency guard, not a Microsoft header.",
            "This emulator implements a bounded update/permission subset; "
            "see integrations/contracts/graph-directory.md.",
        ]
        return descriptor

    async def inspect(self, ctx, mutation):
        if mutation.operation != "update":
            return await self.base.inspect(ctx, mutation)
        action, data, path = self.prepare(mutation)
        transport = await self.transport(ctx, mutation.identity_ref)
        self.check(await transport.request("graph", "GET", path))
        page = await self.base.browse(ctx, ResourceQuery(identifier=data.variables["id"]))
        if not page.items:
            raise AdminError(404, "ResourceNotFound", "Directory resource not found")
        item = page.items[0]
        return Inspection(actual_id=item.id, version=item.version, exists=True)

    async def apply(self, ctx, mutations):
        if not any(m.operation == "update" for m in mutations):
            return await self.base.apply(ctx, mutations)
        if len(mutations) != 1 or not mutations[0].expected_version:
            raise ValueError("Preview one directory update before applying it")
        mutation = mutations[0]
        action, _, path = self.prepare(mutation)
        transport = await self.transport(ctx, mutation.identity_ref)
        response = await transport.request(
            "graph",
            "PATCH",
            path,
            json=mutation.payload["body"],
            headers={"X-MockAzure-If-Version": mutation.expected_version},
        )
        self.check(response)
        result = self.result(action, path, mutation.identity_ref, response)
        result.effects = {"created": 0, "updated": 1, "unchanged": 0}
        return result


def adapter_for(family):
    base = get_adapter(family)
    if family in {"directory.users", "directory.groups"}:
        kind = "user" if family.endswith("users") else "group"
        return DirectoryActions(
            base,
            [
                Action(
                    "update", "graph", "PATCH", "/v1.0/" + kind + "s/{id}", "v1.0", graph_type=kind
                )
            ],
        )
    return base


def reads_for(family, adapter):
    if family in {"directory.users", "directory.groups"}:
        path = "/v1.0/" + family.split(".")[1]
        return {
            "read-list": Action("read-list", "graph", "GET", path, "v1.0"),
            "read-item": Action("read-item", "graph", "GET", path + "/{id}", "v1.0"),
        }
    # Reuse curated collection/resource paths; action suffixes are not read endpoints.
    # Never accept an arbitrary URL from a browser.
    return {
        key + ":read": replace(
            action,
            key=key + ":read",
            path=action.path.removesuffix("/register").removesuffix("/restore"),
            method=adapter.read_method(action),
            params=adapter.read_params(action),
            headers=adapter.read_headers(action),
            binary=False,
            graph_type=None,
            response_kind="json",
        )
        for key, action in getattr(adapter, "actions", {}).items()
    }


class ReadInput(Input):
    operation: str = Field(max_length=128)
    identity_ref: str = Field(min_length=1, max_length=128)
    payload: dict


@router.get("/workbench/catalog")
async def catalog():
    result = []
    for family in list_families():
        adapter = adapter_for(family.key)
        descriptor = adapter.describe().model_dump(mode="json")
        for operation in descriptor["operations"]:
            operation["read"] = False
        for key, action in reads_for(family.key, adapter).items():
            descriptor["operations"].append(
                {
                    "key": key,
                    "mode": "service",
                    "method": action.method,
                    "path_template": action.path,
                    "api_version": action.version,
                    "identity_required": True,
                    "read": True,
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "variables": {"type": "object"},
                            "query": {"type": "object"},
                        },
                    },
                }
            )
        result.append(descriptor)
    return {"families": result}


@router.post("/tenants/{tenant_id}/resources/{family}/read")
async def public_read(tenant_id: UUID, family: str, request: Request):
    data = ReadInput.model_validate(await read_json(request, limit=128 * 1024))
    try:
        adapter = adapter_for(family)
        action = reads_for(family, adapter)[data.operation]
        if set(data.payload) - {"variables", "query"}:
            raise ValueError("Read payload supports variables and query only")
        query = data.payload.get("query", {})
        if (
            not isinstance(query, dict)
            or len(query) > 20
            or any(
                not isinstance(k, str) or not isinstance(v, str) or len(v) > 4096
                for k, v in query.items()
            )
        ):
            raise ValueError("Query parameters must be a bounded string map")
        if any(k in action.params and v != str(action.params[k]) for k, v in query.items()):
            raise ValueError("The operation API version and required parameters are fixed")
        validator = PublicActions(adapter, [action])
        _, _, path = validator.prepare(
            Mutation(
                operation=action.key,
                logical_key="read",
                payload={"variables": data.payload.get("variables", {}), "body": {}},
            )
        )
        ctx = AdapterContext(request.app.state.database, request.app.state.settings, tenant_id)
        transport = await validator.transport(ctx, data.identity_ref)
        transport.shared_key = action.shared_key
        response = await transport.request(
            action.service,
            action.method,
            path,
            params={**action.params, **query},
            headers=action.headers,
        )
    except (KeyError, ValueError, TypeError):
        raise AdminError(
            422, "InvalidWorkbenchRequest", "Unsupported operation or invalid path/query variables"
        ) from None
    except AdminError as error:
        if error.status == 401:
            raise AdminError(
                403, "TestIdentityRejected", "The selected test identity could not authenticate"
            ) from None
        raise
    try:
        body = response.json() if response.content else None
    except ValueError:
        body = response.text
    return {
        "status": response.status_code,
        "body": body,
        "method": action.method,
        "path": path,
        "identity_ref": data.identity_ref,
        "headers": {
            k: v
            for k, v in response.headers.items()
            if k.lower() in {"etag", "request-id", "retry-after", "content-type"}
        },
    }
