"""Bounded relationship queries; recursive UNION removes cycles in PostgreSQL."""

from sqlalchemy import or_, select

from mockazure.directory.models import DirectoryEntry, DirectoryRelation, Group
from mockazure.directory.repository import public_object
from mockazure.identity.models import Application, ServicePrincipal, User
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.permissions import readable_fields
from mockazure.providers.graph.serialization import DEFAULT_FIELDS, serialize_object


def relation_ids(tenant_id, parent_id, resource):
    upward = resource in {"memberOf", "transitiveMemberOf"}
    source = DirectoryRelation.object_id if upward else DirectoryRelation.group_id
    target = DirectoryRelation.group_id if upward else DirectoryRelation.object_id
    relation = "owner" if resource == "owners" else "member"
    base = select(target.label("id")).where(
        DirectoryRelation.tenant_id == tenant_id,
        source == parent_id,
        DirectoryRelation.relation == relation,
    )
    if resource.startswith("transitive"):
        tree = base.cte("directory_tree", recursive=True)
        tree = tree.union(
            select(target)
            .join(tree, source == tree.c.id)
            .where(
                DirectoryRelation.tenant_id == tenant_id,
                DirectoryRelation.relation == "member",
            )
        )
        return select(tree.c.id).where(tree.c.id != parent_id)
    return base


async def relationship_values(session, context, version, parent_id, resource, fields, limit=100):
    ids = relation_ids(context.tenant_id, parent_id, resource)
    if resource != "owners" and "Member.Read.Hidden" not in context.permissions:
        hidden = await session.scalar(
            select(Group.id)
            .where(
                Group.tenant_id == context.tenant_id,
                Group.deleted_at.is_(None),
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
    statement = select(DirectoryEntry).where(
        DirectoryEntry.tenant_id == context.tenant_id, DirectoryEntry.id.in_(ids)
    )
    if version == "v1.0":
        statement = statement.where(DirectoryEntry.kind != "servicePrincipal")
    entries = (await session.scalars(statement.order_by(DirectoryEntry.id).limit(limit + 1))).all()
    if len(entries) > limit:
        raise GraphError(
            400,
            "Request_UnsupportedQuery",
            f"Relationship exceeds local limit {limit}; use a supported paged route",
        )
    result = []
    for entry in entries:
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
            model = User if entry.kind == "user" else Group
            row = await session.scalar(
                select(model).where(
                    model.tenant_id == context.tenant_id,
                    model.id == entry.id,
                    model.deleted_at.is_(None),
                )
            )
            if row is None:
                continue
            item = serialize_object(
                public_object(row), fields or DEFAULT_FIELDS[entry.kind], visible
            )
        item["id"] = str(entry.id)
        item["@odata.type"] = f"#microsoft.graph.{entry.kind}"
        result.append(item)
    return result
