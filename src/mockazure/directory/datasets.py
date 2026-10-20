"""Version-one public datasets: create-only import and bounded snapshot export."""

import json
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StrictBool, field_validator, model_validator
from sqlalchemy import and_, insert, select, tuple_

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.directory.history import reserve_revision
from mockazure.directory.models import (
    DirectoryEntry,
    DirectoryRelation,
    EvidenceOutbox,
    Group,
    ObjectVersion,
    RelationVersion,
)
from mockazure.directory.repository import public_object
from mockazure.identity.models import User
from mockazure.identity.types import IdentityInput, UserCreate
from mockazure.providers.graph.relationships import visible_at

MAX_BYTES = 10 * 1024 * 1024
MAX_OBJECTS = 10000
BATCH_SIZE = 500


class PublicUser(IdentityInput):
    id: UUID
    displayName: str = Field(min_length=1, max_length=200)
    userPrincipalName: str = Field(min_length=3, max_length=320)
    mailNickname: str = Field(min_length=1, max_length=64)
    accountEnabled: StrictBool = True
    userType: Literal["Member", "Guest"] = "Member"
    externalUserState: Literal["PendingAcceptance", "Accepted"] | None = None
    externalUserStateChangeDateTime: AwareDatetime | None = None
    createdDateTime: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    givenName: str | None = Field(default=None, max_length=200)
    surname: str | None = Field(default=None, max_length=200)
    mail: str | None = Field(default=None, max_length=320)
    jobTitle: str | None = Field(default=None, max_length=200)
    department: str | None = Field(default=None, max_length=200)
    officeLocation: str | None = Field(default=None, max_length=200)
    mobilePhone: str | None = Field(default=None, max_length=200)
    businessPhones: list[str] = Field(default_factory=list, max_length=1)
    preferredLanguage: str | None = Field(default=None, max_length=200)

    @field_validator("userPrincipalName")
    @classmethod
    def upn(cls, value):
        return UserCreate.upn_valid(value)

    @model_validator(mode="after")
    def external_state(self):
        if self.userType != "Guest" and self.externalUserState is not None:
            raise ValueError("External acceptance state belongs only to guests")
        return self

    @field_validator("businessPhones")
    @classmethod
    def phones(cls, values):
        if any(not 1 <= len(v) <= 64 or "\x00" in v for v in values):
            raise ValueError("Invalid public phone")
        return values


class PublicGroup(IdentityInput):
    id: UUID
    displayName: str = Field(min_length=1, max_length=200)
    mailNickname: str = Field(min_length=1, max_length=64)
    createdDateTime: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    description: str | None = Field(default=None, max_length=1024)
    mail: str | None = Field(default=None, max_length=320)
    visibility: Literal["Public", "Private", "HiddenMembership"] | None = None
    mailEnabled: StrictBool = False
    securityEnabled: StrictBool = True
    isAssignableToRole: StrictBool = False
    groupTypes: list[str] = Field(default_factory=list, max_length=0)

    @model_validator(mode="after")
    def static_security(self):
        if self.mailEnabled or not self.securityEnabled or self.isAssignableToRole:
            raise ValueError("Only static non-role-assignable security groups are supported")
        return self


class PublicRelationship(IdentityInput):
    group_id: UUID
    object_id: UUID
    relation: Literal["member", "owner"]


class DirectoryDataset(IdentityInput):
    schema_version: Literal[1]
    users: list[PublicUser] = Field(default_factory=list, max_length=MAX_OBJECTS)
    groups: list[PublicGroup] = Field(default_factory=list, max_length=MAX_OBJECTS)
    relationships: list[PublicRelationship] = Field(default_factory=list, max_length=100000)
    provenance: dict = Field(default_factory=lambda: {"source": "mockazure"})

    @model_validator(mode="after")
    def bounded_and_closed(self):
        objects = self.users + self.groups
        if len(objects) > MAX_OBJECTS or len({x.id for x in objects}) != len(objects):
            raise ValueError("Object limit or duplicate ID")
        if len({x.userPrincipalName for x in self.users}) != len(self.users):
            raise ValueError("Duplicate UPN")
        if not self.provenance.keys() <= {"source", "description", "seed", "generator"} or any(
            type(v) not in {str, int}
            or (isinstance(v, str) and (len(v) > 1024 or "\x00" in v))
            or (isinstance(v, int) and not -(2**63) <= v < 2**63)
            for v in self.provenance.values()
        ):
            raise ValueError("Unsupported public provenance")
        users, groups = {x.id for x in self.users}, {x.id for x in self.groups}
        object_ids = users | groups
        edges, graph, incoming = set(), {g: [] for g in groups}, dict.fromkeys(groups, 0)
        for edge in self.relationships:
            key = edge.group_id, edge.object_id, edge.relation
            if key in edges or edge.group_id not in groups or edge.object_id not in object_ids:
                raise ValueError("Invalid, foreign or duplicate relationship")
            edges.add(key)
            if edge.relation == "owner" and edge.object_id not in users:
                raise ValueError("Dataset owners must be included users")
            if edge.relation == "member" and edge.object_id in groups:
                graph[edge.group_id].append(edge.object_id)
                incoming[edge.object_id] += 1
        ready, visited = deque(g for g, degree in incoming.items() if degree == 0), 0
        while ready:
            group = ready.popleft()
            visited += 1
            for child in graph[group]:
                incoming[child] -= 1
                if incoming[child] == 0:
                    ready.append(child)
        if visited != len(groups):
            raise ValueError("Cyclic group membership")
        return self


@dataclass(frozen=True)
class ImportResult:
    objects: int
    relationships: int
    revision: int


async def bulk_insert(session, model, rows):
    for offset in range(0, len(rows), BATCH_SIZE):
        await session.execute(insert(model), rows[offset : offset + BATCH_SIZE])


async def import_directory(
    session, tenant_id, dataset: DirectoryDataset, mode="create"
) -> ImportResult:
    if mode != "create":
        raise AdminError(400, "UnsupportedImportMode", "Directory import is create-only")
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")
    if any(user.userPrincipalName.split("@")[1] != tenant.domain.lower() for user in dataset.users):
        raise AdminError(400, "InvalidDataset", "Dataset UPN domains must match the target tenant")
    revision = await reserve_revision(session, tenant_id)
    user_rows, group_rows, entries, versions = [], [], [], []
    membership = {e.group_id for e in dataset.relationships if e.relation == "member"}
    ownership = {e.group_id for e in dataset.relationships if e.relation == "owner"}
    for value in dataset.users + dataset.groups:
        properties = value.model_dump(mode="json")
        if isinstance(value, PublicUser):
            row = User(
                id=value.id,
                tenant_id=tenant_id,
                upn=value.userPrincipalName,
                display_name=value.displayName,
                enabled=value.accountEnabled,
                verifier="!",
                profile={
                    k: v
                    for k, v in properties.items()
                    if k
                    not in {
                        "id",
                        "displayName",
                        "userPrincipalName",
                        "accountEnabled",
                        "createdDateTime",
                    }
                },
                created_at=value.createdDateTime,
            )
            user_rows.append(
                dict(
                    id=row.id,
                    tenant_id=tenant_id,
                    upn=row.upn,
                    display_name=row.display_name,
                    enabled=row.enabled,
                    verifier="!",
                    profile=row.profile,
                    created_at=row.created_at,
                )
            )
            kind, column = "user", "user_id"
        else:
            row = Group(
                id=value.id,
                tenant_id=tenant_id,
                display_name=value.displayName,
                mail_nickname=value.mailNickname,
                created_at=value.createdDateTime,
                profile={
                    k: v
                    for k, v in properties.items()
                    if k not in {"id", "displayName", "mailNickname", "createdDateTime"}
                },
            )
            group_rows.append(
                dict(
                    id=row.id,
                    tenant_id=tenant_id,
                    display_name=row.display_name,
                    mail_nickname=row.mail_nickname,
                    profile=row.profile,
                    created_at=row.created_at,
                )
            )
            kind, column = "group", "group_id"
        public = public_object(row).properties
        fields = set(public)
        if row.id in membership:
            fields.add("members")
        if row.id in ownership:
            fields.add("owners")
        entries.append(dict(tenant_id=tenant_id, id=row.id, kind=kind, **{column: row.id}))
        versions.append(
            dict(
                tenant_id=tenant_id,
                object_id=row.id,
                kind=kind,
                valid_from=revision,
                properties=public,
                changed_fields=sorted(fields),
                removed=False,
                display_name=row.display_name,
                upn=public.get("userPrincipalName"),
                account_enabled=public.get("accountEnabled"),
                mail_nickname=public.get("mailNickname"),
                created_at=row.created_at,
            )
        )
    await bulk_insert(session, User, user_rows)
    await bulk_insert(session, Group, group_rows)
    await bulk_insert(session, DirectoryEntry, entries)
    await bulk_insert(session, ObjectVersion, versions)
    edges = [dict(tenant_id=tenant_id, **e.model_dump()) for e in dataset.relationships]
    await bulk_insert(session, DirectoryRelation, edges)
    await bulk_insert(session, RelationVersion, [dict(e, valid_from=revision) for e in edges])
    session.add(
        EvidenceOutbox(
            tenant_id=tenant_id,
            object_id=tenant_id,
            kind="directory",
            revision=revision,
            facts={
                "schema_version": 1,
                "operation": "import",
                "provenance": dataset.provenance,
                "target_ids": [str(x.id) for x in dataset.users + dataset.groups],
                "object_count": len(entries),
                "relationship_count": len(edges),
            },
        )
    )
    await session.flush()
    return ImportResult(len(entries), len(edges), revision)


async def validate_export(session, tenant_id, snapshot):
    if await session.get(Tenant, tenant_id) is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")
    external = await session.scalar(
        select(RelationVersion.object_id)
        .join(
            DirectoryEntry,
            and_(
                DirectoryEntry.tenant_id == RelationVersion.tenant_id,
                DirectoryEntry.id == RelationVersion.object_id,
            ),
        )
        .where(
            RelationVersion.tenant_id == tenant_id,
            *visible_at(RelationVersion, snapshot),
            DirectoryEntry.kind == "servicePrincipal",
        )
        .limit(1)
    )
    if external is not None:
        raise AdminError(
            409,
            "UnsupportedDatasetReference",
            "Version-one datasets require user/group references; "
            "service principal datasets are deferred",
        )


async def export_directory(session, tenant_id, snapshot):
    """Caller retains its history read lock and transaction throughout iteration."""
    provenance = await session.scalar(
        select(EvidenceOutbox.facts["provenance"])
        .where(
            EvidenceOutbox.tenant_id == tenant_id,
            EvidenceOutbox.kind == "directory",
            EvidenceOutbox.revision <= snapshot,
            EvidenceOutbox.facts["operation"].as_string() == "import",
        )
        .order_by(EvidenceOutbox.revision.desc())
        .limit(1)
    )
    yield '{"schema_version":1,"provenance":' + json.dumps(provenance or {"source": "mockazure"})
    for kind in ("user", "group"):
        yield ',"' + kind + 's":['
        after, comma = None, ""
        while True:
            conditions = [
                ObjectVersion.tenant_id == tenant_id,
                ObjectVersion.kind == kind,
                *visible_at(ObjectVersion, snapshot),
                ObjectVersion.removed.is_(False),
            ]
            if after:
                conditions.append(ObjectVersion.object_id > after)
            rows = (
                await session.execute(
                    select(ObjectVersion.object_id, ObjectVersion.properties)
                    .where(*conditions)
                    .order_by(ObjectVersion.object_id)
                    .limit(BATCH_SIZE)
                )
            ).all()
            if not rows:
                break
            fields = PublicUser.model_fields if kind == "user" else PublicGroup.model_fields
            yield comma + ",".join(
                json.dumps(
                    {k: v for k, v in r.properties.items() if k in fields}, ensure_ascii=False
                )
                for r in rows
            )
            comma, after = ",", rows[-1].object_id
        yield "]"
    yield ',"relationships":['
    after, comma = None, ""
    keys = (RelationVersion.group_id, RelationVersion.object_id, RelationVersion.relation)
    while True:
        conditions = [
            RelationVersion.tenant_id == tenant_id,
            *visible_at(RelationVersion, snapshot),
        ]
        if after:
            conditions.append(tuple_(*keys) > after)
        rows = (
            await session.execute(
                select(*keys).where(*conditions).order_by(*keys).limit(BATCH_SIZE)
            )
        ).all()
        if not rows:
            break
        yield comma + ",".join(
            json.dumps(
                {
                    "group_id": str(r.group_id),
                    "object_id": str(r.object_id),
                    "relation": r.relation,
                }
            )
            for r in rows
        )
        comma, after = ",", tuple(rows[-1])
    yield "]}"
