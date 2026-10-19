"""Shared bounded reads and conditional writes for public-payload repositories."""

from dataclasses import dataclass

from fastapi.encoders import jsonable_encoder
from sqlalchemy import literal, select, tuple_

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.population.tokens import decode, fingerprint, sign
from mockazure.population.types import (
    BatchResult,
    Inspection,
    ResourceItem,
    ResourcePage,
    ResourceQuery,
    VerificationResult,
)


@dataclass(frozen=True)
class ReadSpec:
    model: object
    identifier: object
    payload: object
    version: object
    conditions: tuple = ()
    parent: object = None


def coerce(column, value):
    try:
        return column.type.python_type(value)
    except (ValueError, TypeError):
        raise AdminError(422, "InvalidIdentifier", "Invalid resource identifier") from None


class PayloadAdapter:
    identity_field = "id"

    def __init__(self, descriptor, read_spec, normalizer, writer, *, replace_allowed=False):
        self.descriptor = descriptor
        self.spec = read_spec
        self.normalizer = normalizer
        self.writer = writer
        self.replace_allowed = replace_allowed

    def describe(self):
        return self.descriptor

    def normalize(self, mutation):
        if mutation.operation not in {x.key for x in self.descriptor.operations}:
            raise ValueError("Unsupported resource operation")
        return self.normalizer(mutation.payload)

    def validate(self, mutation):
        self.normalize(mutation)

    def conditions(self, ctx, query):
        spec = self.spec
        values = [spec.model.tenant_id == ctx.tenant_id, *spec.conditions]
        if query.parent is not None:
            if spec.parent is None:
                raise AdminError(422, "InvalidParent", "This family has no parent selector")
            values.append(spec.parent == coerce(spec.parent, query.parent))
        if query.identifier is not None:
            values.append(spec.identifier == coerce(spec.identifier, query.identifier))
        for field, value in query.filters.items():
            if field not in self.descriptor.display_fields or isinstance(value, (list, dict)):
                raise AdminError(
                    422, "UnsupportedFilter", "Only displayed scalar equality is supported"
                )
            values.append(spec.payload[field] == value)
        return values

    async def browse_session(self, session, ctx, query, *, lock=False):
        if await session.get(Tenant, ctx.tenant_id) is None:
            raise AdminError(404, "TenantNotFound", "Tenant not found")
        spec = self.spec
        binding = {
            "tenant": str(ctx.tenant_id),
            "family": self.descriptor.key,
            "query": query.model_dump(exclude={"continuation"}),
        }
        columns = [spec.identifier.label("_id"), spec.version.label("_revision")]
        columns.append((spec.parent if spec.parent is not None else literal(None)).label("_parent"))
        if query.identifier is not None:
            columns.append(spec.payload.label("_payload"))
        else:
            columns.extend(
                spec.payload[field].label(field) for field in self.descriptor.display_fields
            )
        ordering = ([spec.parent] if spec.parent is not None else []) + [spec.identifier]
        conditions = self.conditions(ctx, query)
        if query.continuation:
            cursor = decode(ctx.settings, "resource-page", query.continuation)
            if cursor.get("binding") != binding or len(cursor.get("last", [])) != len(ordering):
                raise AdminError(400, "InvalidContinuation", "Continuation scope changed")
            last = [coerce(column, value) for column, value in zip(ordering, cursor["last"])]
            conditions.append(tuple_(*ordering) > tuple_(*last))
        statement = select(*columns).where(*conditions).order_by(*ordering).limit(query.limit + 1)
        if lock:
            statement = statement.with_for_update()
        rows = list((await session.execute(statement)).mappings())
        items = []
        for row in rows[: query.limit]:
            properties = (
                row["_payload"]
                if query.identifier is not None
                else {key: row[key] for key in self.descriptor.display_fields}
            )
            identifier = str(row["_id"])
            parent = str(row["_parent"]) if row["_parent"] is not None else None
            items.append(
                ResourceItem(
                    id=identifier,
                    properties=jsonable_encoder(properties),
                    parent=parent,
                    version=fingerprint(
                        [
                            str(ctx.tenant_id),
                            self.descriptor.key,
                            parent,
                            identifier,
                            row["_revision"],
                        ]
                    ),
                )
            )
        continuation = None
        if len(rows) > query.limit:
            row = rows[query.limit - 1]
            last = ([row["_parent"]] if spec.parent is not None else []) + [row["_id"]]
            continuation = sign(ctx.settings, "resource-page", {"binding": binding, "last": last})
        return ResourcePage(items=items, continuation=continuation)

    async def browse(self, ctx, query):
        async with ctx.database.sessions.begin() as session:
            return await self.browse_session(session, ctx, query)

    async def inspect(self, ctx, mutation):
        payload = self.normalize(mutation)
        page = await self.browse(
            ctx, ResourceQuery(identifier=str(payload[self.identity_field]), parent=mutation.parent)
        )
        if not page.items:
            return Inspection(actual_id=None, version=None, exists=False)
        if len(page.items) != 1:
            raise AdminError(422, "AmbiguousResource", "Select the parent resource")
        item = page.items[0]
        return Inspection(actual_id=item.id, version=item.version, exists=True)

    def matches(self, existing, desired):
        return all(existing.get(key) == value for key, value in desired.items())

    async def matches_mutation(self, session, ctx, old, mutation, payload):
        return self.matches(old.properties, payload)

    async def apply(self, ctx, mutations):
        if not 1 <= len(mutations) <= 1000:
            raise ValueError("Expected 1 to 1000 mutations")
        prepared = [(item, self.normalize(item)) for item in mutations]
        if len({(m.parent, str(p[self.identity_field])) for m, p in prepared}) != len(prepared):
            raise ValueError("Duplicate resource in batch")
        effects = {"created": 0, "updated": 0, "unchanged": 0}
        pending, items = [], []
        async with ctx.database.sessions.begin() as session:
            for mutation, payload in prepared:
                query = ResourceQuery(
                    identifier=str(payload[self.identity_field]), parent=mutation.parent
                )
                page = await self.browse_session(session, ctx, query, lock=True)
                if len(page.items) > 1:
                    raise AdminError(422, "AmbiguousResource", "Select the parent resource")
                old = page.items[0] if page.items else None
                if mutation.expected_version and mutation.expected_version != (
                    old.version if old else "absent"
                ):
                    raise AdminError(409, "ResourceConflict", "Resource changed; preview again")
                if old and await self.matches_mutation(session, ctx, old, mutation, payload):
                    effects["unchanged"] += 1
                    items.append(old)
                    continue
                if old and (not mutation.expected_version or not self.replace_allowed):
                    raise AdminError(
                        409, "ResourceConflict", "Explicit supported replacement required"
                    )
                effects["updated" if old else "created"] += 1
                pending.append((mutation, payload))
            if pending:
                await self.writer(session, ctx, pending)
                await session.flush()
                for mutation, payload in pending:
                    page = await self.browse_session(
                        session,
                        ctx,
                        ResourceQuery(
                            identifier=str(payload[self.identity_field]), parent=mutation.parent
                        ),
                    )
                    items.extend(page.items)
        return BatchResult(items=items, effects=effects)

    async def verify(self, ctx, result):
        actual = 0
        for expected in result.items:
            page = await self.browse(
                ctx, ResourceQuery(identifier=expected.id, parent=expected.parent)
            )
            if len(page.items) == 1 and page.items[0].version == expected.version:
                actual += 1
        return VerificationResult(
            status="succeeded" if actual == len(result.items) else "failed",
            expected=len(result.items),
            actual=actual,
        )
