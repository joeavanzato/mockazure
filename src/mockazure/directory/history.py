"""Commit-ordered public revisions. Callers own transaction commit/rollback."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mockazure.directory.models import DirectoryEntry, DirectoryState, EvidenceOutbox, ObjectVersion
from mockazure.directory.types import FACT_FIELDS, PUBLIC_FIELDS, DirectoryChange, DirectoryObject


async def reserve_revision(session: AsyncSession, tenant_id: UUID) -> int:
    await session.execute(
        insert(DirectoryState)
        .values(tenant_id=tenant_id, revision=0, generation=uuid4())
        .on_conflict_do_nothing(index_elements=["tenant_id"])
    )
    # UPDATE takes a row lock held until the caller's commit, including first-writer creation.
    return await session.scalar(
        update(DirectoryState)
        .where(DirectoryState.tenant_id == tenant_id)
        .values(revision=DirectoryState.revision + 1)
        .returning(DirectoryState.revision)
    )


async def record_change(
    session: AsyncSession,
    change: DirectoryChange,
    before: DirectoryObject | None,
    after: DirectoryObject | None,
    facts: dict,
) -> None:
    obj = after or before
    if obj is None or change.kind not in PUBLIC_FIELDS:
        raise ValueError("A public directory object is required")
    allowed = PUBLIC_FIELDS[change.kind]
    if not change.changed_fields <= allowed | {"members", "owners"}:
        raise ValueError("History accepts public fields only")
    for value in (before, after):
        if value is not None and (
            value.kind != change.kind
            or value.id != change.object_id
            or value.tenant_id != obj.tenant_id
            or not value.properties.keys() <= allowed
            or value.properties.get("id") != str(value.id)
        ):
            raise ValueError("History accepts matching public objects only")
    if not facts.keys() <= FACT_FIELDS:
        raise ValueError("Evidence accepts public facts only")
    if change.removed != (after is None):
        raise ValueError("Removal and public state disagree")
    entry = {
        "tenant_id": obj.tenant_id,
        "id": obj.id,
        "kind": obj.kind,
        "user_id" if obj.kind == "user" else "group_id": obj.id,
    }
    await session.execute(
        insert(DirectoryEntry)
        .values(**entry)
        .on_conflict_do_nothing(index_elements=["tenant_id", "id"])
    )
    await session.execute(
        update(ObjectVersion)
        .where(
            ObjectVersion.tenant_id == obj.tenant_id,
            ObjectVersion.object_id == obj.id,
            ObjectVersion.valid_to.is_(None),
        )
        .values(valid_to=change.revision)
    )
    properties = obj.properties
    session.add(
        ObjectVersion(
            tenant_id=obj.tenant_id,
            object_id=obj.id,
            kind=obj.kind,
            valid_from=change.revision,
            properties=properties,
            changed_fields=sorted(change.changed_fields),
            removed=change.removed,
            display_name=properties.get("displayName"),
            upn=properties.get("userPrincipalName"),
            account_enabled=properties.get("accountEnabled"),
            mail_nickname=properties.get("mailNickname"),
            created_at=datetime.fromisoformat(properties["createdDateTime"])
            if properties.get("createdDateTime")
            else None,
        )
    )
    session.add(
        EvidenceOutbox(
            tenant_id=obj.tenant_id,
            object_id=obj.id,
            kind=obj.kind,
            revision=change.revision,
            facts={"schema_version": 1, **facts},
        )
    )
    await session.flush()


async def current_revision(session: AsyncSession, tenant_id: UUID) -> int:
    return (
        await session.scalar(
            select(DirectoryState.revision).where(DirectoryState.tenant_id == tenant_id)
        )
        or 0
    )
