"""Canonical ownership navigation, projected before snapshot materialization."""

from sqlalchemy import String, cast, literal, select, union_all

from mockazure.directory.models import DirectoryRelation, Group
from mockazure.providers.graph.permissions import readable_fields
from mockazure.security.models import SecurityOwner
from mockazure.security.repository import document, security_source


def owned_source(actor, user_id):
    visible = "displayName" in readable_fields(actor, "group")
    groups = select(
        Group.id.label("id"),
        document(
            id=cast(Group.id, String),
            displayName=Group.display_name if visible else literal(None, String),
            **{"@odata.type": "#microsoft.graph.group"},
        ).label("payload"),
    ).where(
        Group.tenant_id == actor.tenant_id,
        Group.deleted_at.is_(None),
        Group.id.in_(
            select(DirectoryRelation.group_id).where(
                DirectoryRelation.tenant_id == actor.tenant_id,
                DirectoryRelation.object_id == user_id,
                DirectoryRelation.relation == "owner",
            )
        ),
    )
    sources = [groups]
    for kind in ("application", "servicePrincipal", "device"):
        source = security_source(actor.tenant_id, kind)
        readable = actor.permissions & (
            {"Device.Read.All", "Device.ReadWrite.All"}
            if kind == "device"
            else {"Application.Read.All", "Application.ReadWrite.All"}
        )
        readable |= actor.permissions & {"Directory.Read.All", "Directory.ReadWrite.All"}
        sources.append(
            select(
                source.c.id,
                document(
                    id=cast(source.c.id, String),
                    displayName=source.c.payload["displayName"].as_string()
                    if readable
                    else literal(None, String),
                    **{"@odata.type": "#microsoft.graph." + kind},
                ).label("payload"),
            ).where(
                source.c.id.in_(
                    select(SecurityOwner.object_id).where(
                        SecurityOwner.tenant_id == actor.tenant_id,
                        SecurityOwner.owner_id == user_id,
                    )
                )
            )
        )
    return union_all(*sources).subquery()
