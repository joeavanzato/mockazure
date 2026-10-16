"""Snapshot relationship traversal and projection, bounded in PostgreSQL."""

from dataclasses import dataclass

from sqlalchemy import and_, or_, select

from mockazure.directory.history import current_revision
from mockazure.directory.models import (
    DirectoryEntry,
    DirectoryRelation,
    Group,
    ObjectVersion,
    RelationVersion,
)
from mockazure.directory.types import DirectoryObject
from mockazure.identity.models import Application, ServicePrincipal
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.permissions import readable_fields
from mockazure.providers.graph.serialization import DEFAULT_FIELDS, serialize_object


def visible_at(model, snapshot):
    return (model.valid_from <= snapshot, or_(model.valid_to.is_(None), model.valid_to > snapshot))


def relation_ids(tenant_id, parent_id, resource, snapshot=None):
    model = DirectoryRelation if snapshot is None else RelationVersion
    temporal = () if snapshot is None else visible_at(model, snapshot)
    upward = resource in {"memberOf", "transitiveMemberOf"}
    source = model.object_id if upward else model.group_id
    target = model.group_id if upward else model.object_id
    relation = "owner" if resource == "owners" else "member"
    conditions = (model.tenant_id == tenant_id, model.relation == relation, *temporal)
    base = select(target.label("id")).where(source == parent_id, *conditions)
    if resource.startswith("transitive"):
        tree = base.cte(recursive=True)
        tree = tree.union(select(target).join(tree, source == tree.c.id).where(*conditions))
        return select(tree.c.id).where(tree.c.id != parent_id)
    return base


async def authorize_membership_visibility(session, context, parent_id, resource, snapshot=None):
    if resource == "owners" or "Member.Read.Hidden" in context.permissions:
        return
    # Check current policy for every group in both the live and historical traversal.
    # Soft deletion must not erase hidden-membership protection for retained edges.
    ids = relation_ids(context.tenant_id, parent_id, resource)
    if snapshot is not None:
        ids = ids.union(relation_ids(context.tenant_id, parent_id, resource, snapshot))
    hidden = await session.scalar(
        select(Group.id)
        .where(
            Group.tenant_id == context.tenant_id,
            Group.profile["visibility"].as_string() == "HiddenMembership",
            or_(
                Group.id == parent_id,
                Group.id.in_(ids)
                if resource in {"transitiveMembers", "memberOf", "transitiveMemberOf"}
                else False,
            ),
        )
        .limit(1)
    )
    if hidden is not None:
        raise GraphError(
            403, "Authorization_RequestDenied", "Hidden membership permission is required"
        )


@dataclass(frozen=True)
class RelationshipPage:
    value: list[dict]
    last_id: str | None
    has_more: bool


async def relationship_page(
    session, context, version, parent_id, resource, fields, snapshot, after=None, limit=100
):
    await authorize_membership_visibility(session, context, parent_id, resource, snapshot)
    ids = relation_ids(context.tenant_id, parent_id, resource, snapshot)
    statement = (
        select(DirectoryEntry, ObjectVersion.properties)
        .outerjoin(
            ObjectVersion,
            and_(
                ObjectVersion.tenant_id == DirectoryEntry.tenant_id,
                ObjectVersion.object_id == DirectoryEntry.id,
                *visible_at(ObjectVersion, snapshot),
                ObjectVersion.removed.is_(False),
            ),
        )
        .where(
            DirectoryEntry.tenant_id == context.tenant_id,
            DirectoryEntry.id.in_(ids),
            or_(DirectoryEntry.kind == "servicePrincipal", ObjectVersion.object_id.is_not(None)),
        )
    )
    if version == "v1.0":
        statement = statement.where(DirectoryEntry.kind != "servicePrincipal")
    if after is not None:
        from uuid import UUID

        statement = statement.where(DirectoryEntry.id > UUID(after))
    rows = (await session.execute(statement.order_by(DirectoryEntry.id).limit(limit + 1))).all()
    result = []
    for entry, properties in rows[:limit]:
        visible = readable_fields(context, entry.kind, entry.id)
        if entry.kind == "servicePrincipal":
            sp = await session.get(ServicePrincipal, entry.id)
            app = await session.get(Application, sp.client_id)
            data = {
                "id": str(sp.id),
                "displayName": app.display_name,
                "appId": str(sp.client_id),
                "accountEnabled": sp.enabled,
            }
            chosen = fields or tuple(data)
            if not set(chosen) <= data.keys():
                raise GraphError(400, "Request_UnsupportedQuery", "Unsupported member property")
            item = {key: data[key] if key in visible else None for key in chosen}
        else:
            item = serialize_object(
                DirectoryObject(entry.kind, entry.id, entry.tenant_id, properties),
                fields or DEFAULT_FIELDS[entry.kind],
                visible,
            )
        item["id"] = str(entry.id)
        item["@odata.type"] = f"#microsoft.graph.{entry.kind}"
        result.append(item)
    return RelationshipPage(result, result[-1]["id"] if result else None, len(rows) > limit)


async def relationship_values(
    session, context, version, parent_id, resource, fields, limit=100, snapshot=None
):
    if snapshot is None:
        snapshot = await current_revision(session, context.tenant_id)
    page = await relationship_page(
        session, context, version, parent_id, resource, fields, snapshot, limit=limit
    )
    if page.has_more:
        raise GraphError(
            400,
            "Request_UnsupportedQuery",
            f"Expansion exceeds local limit {limit}; use the paged relationship route",
        )
    return page.value
