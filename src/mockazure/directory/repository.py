"""Public projections and bounded SQL directory reads."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, false, func, or_, select

from mockazure.directory.models import Group, ObjectVersion
from mockazure.directory.types import GROUP_FIELDS, USER_FIELDS, DirectoryObject
from mockazure.identity.models import User
from mockazure.providers.graph.query.ast import QuerySpec
from mockazure.providers.graph.query.compiler import compile_filter, typed_value
from mockazure.providers.graph.query.contracts import contract_for, field_column


def public_object(row: User | Group) -> DirectoryObject:
    if isinstance(row, User):
        properties = dict.fromkeys(USER_FIELDS)
        properties.update({k: v for k, v in row.profile.items() if k in USER_FIELDS})
        properties.update(
            id=str(row.id),
            displayName=row.display_name,
            userPrincipalName=row.upn,
            accountEnabled=row.enabled,
            userType=row.profile.get("userType", "Member"),
            mailNickname=row.profile.get("mailNickname", row.upn.partition("@")[0][:64]),
            businessPhones=row.profile.get("businessPhones", []),
            assignedLicenses=row.profile.get("assignedLicenses", []),
            assignedPlans=row.profile.get("assignedPlans", []),
            createdDateTime=row.created_at.isoformat().replace("+00:00", "Z"),
        )
        kind = "user"
    else:
        properties = dict.fromkeys(GROUP_FIELDS)
        properties.update(
            groupTypes=[],
            mailEnabled=False,
            securityEnabled=True,
            isAssignableToRole=False,
        )
        properties.update({k: v for k, v in row.profile.items() if k in GROUP_FIELDS})
        properties.update(
            id=str(row.id),
            displayName=row.display_name,
            mailNickname=row.mail_nickname,
            createdDateTime=row.created_at.isoformat().replace("+00:00", "Z"),
        )
        kind = "group"
    return DirectoryObject(kind, row.id, row.tenant_id, properties)


@dataclass(frozen=True)
class DirectoryPage:
    items: list[DirectoryObject]
    last_key: tuple | None
    has_more: bool
    total: int | None


async def read_page(
    session,
    tenant_id: UUID,
    kind: str,
    query: QuerySpec,
    snapshot: int,
    after: tuple | None,
    limit: int,
) -> DirectoryPage:
    if not 1 <= limit <= 999:
        raise ValueError("Directory page limit must be 1..999")
    contract = contract_for("v1.0", kind + "s")
    conditions = [
        ObjectVersion.tenant_id == tenant_id,
        ObjectVersion.kind == kind,
        ObjectVersion.valid_from <= snapshot,
        or_(ObjectVersion.valid_to.is_(None), ObjectVersion.valid_to > snapshot),
        ObjectVersion.removed.is_(False),
    ]
    if query.filter is not None:
        conditions.append(compile_filter(query.filter, contract))
    total = (
        await session.scalar(select(func.count()).select_from(ObjectVersion).where(*conditions))
        if query.count
        else None
    )
    columns = [field_column(contract.fields[term.field]) for term in query.order]
    if after is not None:
        if len(after) != len(columns):
            raise ValueError("Invalid continuation key")
        equal, candidates = [], []
        for column, term, raw in zip(columns, query.order, after, strict=True):
            value = typed_value(raw, contract.fields[term.field]) if raw is not None else None
            greater = (
                false()
                if value is None
                else or_(
                    column > value if term.direction == "asc" else column < value,
                    column.is_(None),
                )
            )
            candidates.append(and_(*equal, greater))
            equal.append(column.is_(None) if value is None else column == value)
        conditions.append(or_(*candidates))
    projection = ObjectVersion.properties
    if query.select is not None:
        pairs = []
        for field in query.select:
            pairs.extend((field, ObjectVersion.properties[field]))
        projection = func.jsonb_build_object(*pairs)
    statement = select(ObjectVersion.object_id, projection, *columns).where(*conditions)
    statement = statement.order_by(
        *[
            (column.asc() if term.direction == "asc" else column.desc()).nulls_last()
            for column, term in zip(columns, query.order, strict=True)
        ]
    ).limit(limit + 1)
    rows = (await session.execute(statement)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    def key(value):
        return (
            value.isoformat()
            if isinstance(value, datetime)
            else str(value)
            if isinstance(value, UUID)
            else value
        )

    return DirectoryPage(
        [DirectoryObject(kind, row[0], tenant_id, row[1]) for row in rows],
        tuple(key(value) for value in rows[-1][2:]) if rows else None,
        has_more,
        total,
    )
