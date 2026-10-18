"""Revisioned workload repository; public adapters supply authorized SQL predicates."""

import copy
import hashlib
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from mockazure.admin.errors import AdminError
from mockazure.content.repository import object_row, pin_content
from mockazure.directory.models import Group
from mockazure.identity.models import User
from mockazure.workloads.imports import ImportResult, WorkloadImport
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadScope as S
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.sites import EDGES, validate_site_drive_record


@dataclass(frozen=True)
class CompiledQuery:
    predicate: object
    ordering: tuple = ()
    projection: tuple[str, ...] | None = None
    limit: int = 100
    offset: int = 0


def visible(model, revision):
    return and_(
        model.valid_from <= revision, or_(model.valid_to.is_(None), model.valid_to > revision)
    )


def pin_owner(workload, scope, kind, identifier, revision):
    return hashlib.sha256(repr((workload, scope, kind, identifier, revision)).encode()).hexdigest()


async def scope_row(session, tenant, workload, scope, *, create=False, lock=False):
    if create:
        await session.execute(
            insert(S)
            .values(
                tenant_id=tenant,
                workload=workload,
                scope=scope,
                revision=0,
                generation=uuid4(),
                grant_revision=0,
            )
            .on_conflict_do_nothing()
        )
    query = select(S).where(S.tenant_id == tenant, S.workload == workload, S.scope == scope)
    if lock:
        query = query.with_for_update(read=lock == "read")
    return await session.scalar(query.execution_options(populate_existing=True))


async def current_record(session, tenant, workload, kind, identifier):
    return await session.scalar(
        select(V).where(
            V.tenant_id == tenant,
            V.workload == workload,
            V.kind == kind,
            V.id == identifier,
            V.valid_to.is_(None),
            V.deleted.is_(False),
        )
    )


async def list_versions(session, tenant_id, workload, scope, kind, revision, query):
    if query.predicate is None or not 1 <= query.limit <= 1001 or query.offset < 0:
        raise ValueError("A bounded authorized workload query is required")
    rows = await session.scalars(
        select(V)
        .where(
            V.tenant_id == tenant_id,
            V.workload == workload,
            V.scope == scope,
            V.kind == kind,
            visible(V, revision),
            V.deleted.is_(False),
            query.predicate,
        )
        .order_by(*query.ordering, V.id)
        .offset(query.offset)
        .limit(query.limit)
    )
    return [
        {
            key: value
            for key, value in row.payload.items()
            if query.projection is None or key == "id" or key in query.projection
        }
        for row in rows
    ]


async def related(session, tenant, workload, scope, source_kind, source_id, relation, revision):
    return list(
        await session.scalars(
            select(V)
            .join(
                E,
                and_(
                    E.tenant_id == V.tenant_id,
                    E.workload == V.workload,
                    E.scope == V.scope,
                    E.target_kind == V.kind,
                    E.target_id == V.id,
                ),
            )
            .where(
                E.tenant_id == tenant,
                E.workload == workload,
                E.scope == scope,
                E.source_kind == source_kind,
                E.source_id == source_id,
                E.relation == relation,
                visible(E, revision),
                visible(V, revision),
                V.deleted.is_(False),
            )
            .order_by(V.id)
        )
    )


async def import_workload(session, tenant_id: UUID, workload: str, batch: WorkloadImport):
    if workload != "sharepoint":
        raise AdminError(422, "UnsupportedWorkload", "Unsupported workload import")
    if batch.manifest is not None:
        from mockazure.workloads.lifecycle import restored_records

        batch = batch.model_copy(
            update={"records": await restored_records(session, tenant_id, workload, batch)}
        )
    for record in batch.records:
        if record.payload.get("id") != record.id:
            raise AdminError(422, "InvalidIdentity", "Payload id must match record identity")
        if record.kind in {"site", "drive"} and record.key()[1] != record.id:
            raise AdminError(422, "InvalidIdentity", "Site and drive keys equal their public IDs")
        validate_site_drive_record(record.kind, record.payload)
        if record.content_id and (
            record.kind not in {"driveItem", "driveItemVersion"}
            or (record.kind == "driveItem" and "file" not in record.payload)
        ):
            raise AdminError(422, "InvalidContent", "Only file items and versions can have bytes")
        if any(c in record.id for c in "/\\") or record.id in {".", ".."}:
            raise AdminError(422, "InvalidIdentity", "Resource IDs cannot contain path separators")
    state = await scope_row(session, tenant_id, workload, batch.scope, create=True, lock=True)
    if batch.expected_revision is not None and batch.expected_revision != state.revision:
        raise AdminError(409, "WorkloadRevisionConflict", "Workload revision changed")
    existing = {
        (row.kind, row.id): row
        for row in await session.execute(
            select(
                V.kind,
                V.id,
                V.name,
                V.payload["id"].as_string().label("public_id"),
                V.payload["folder"].label("folder"),
                V.payload["webUrl"].as_string().label("url"),
            ).where(
                V.tenant_id == tenant_id,
                V.workload == workload,
                V.scope == batch.scope,
                V.valid_to.is_(None),
                V.deleted.is_(False),
            )
        )
    }
    incoming = {record.key(): record for record in batch.records}
    deleted = {key.key() for key in batch.tombstones}
    if deleted - existing.keys():
        raise AdminError(404, "WorkloadRecordNotFound", "Tombstone record does not exist")
    keys = (existing.keys() | incoming.keys()) - deleted
    if ("site", batch.scope) not in keys and keys:
        raise AdminError(422, "InvalidScope", "Site scope must contain its canonical site")
    if any(kind == "site" and identifier != batch.scope for kind, identifier in keys):
        raise AdminError(422, "InvalidScope", "Only the canonical site belongs to this scope")
    for key in incoming:
        if key[0] not in {"site", "drive"}:
            continue
        other = await session.scalar(
            select(V.scope).where(
                V.tenant_id == tenant_id,
                V.workload == workload,
                V.kind == key[0],
                V.id == key[1],
                V.valid_to.is_(None),
            )
        )
        if other is not None and other != batch.scope:
            raise AdminError(
                422, "IdentityScopeConflict", "Resource identity belongs to another scope"
            )
    public_ids = {key: row.public_id for key, row in existing.items()}
    for key, record in incoming.items():
        if key in public_ids and public_ids[key] != record.id:
            raise AdminError(422, "InvalidIdentity", "A stable record key cannot change public ID")
        public_ids[key] = record.id
    for edge in batch.edges:
        for endpoint in (edge.source, edge.target):
            if (
                endpoint.kind not in {"user", "group"}
                and public_ids.get(endpoint.key()) != endpoint.id
            ):
                raise AdminError(422, "InvalidRelationship", "Edge key and public ID do not match")
    for endpoint in batch.tombstones:
        if public_ids.get(endpoint.key()) != endpoint.id:
            raise AdminError(422, "InvalidIdentity", "Tombstone key and public ID do not match")
    old_edges = list(
        await session.scalars(
            select(E).where(
                E.tenant_id == tenant_id,
                E.workload == workload,
                E.scope == batch.scope,
                E.valid_to.is_(None),
            )
        )
    )
    # An imported source replaces its outgoing edges; deleting a record removes incident edges.
    keep, close = [], []
    for edge in old_edges:
        source, target = (edge.source_kind, edge.source_id), (edge.target_kind, edge.target_id)
        if source in incoming or source in deleted or target in deleted:
            close.append(edge)
        else:
            keep.append((source, edge.relation, target))
    new_edges = [(edge.source.key(), edge.relation, edge.target.key()) for edge in batch.edges]
    all_edges = keep + new_edges
    if len(set(all_edges)) != len(all_edges):
        raise AdminError(422, "DuplicateRelationship", "Relationship already exists")
    parent, roots, paths, versions, public_children = {}, {}, set(), set(), set()
    for source, relation, target in all_edges:
        if (source[0], relation, target[0]) not in EDGES or target not in keys:
            raise AdminError(422, "InvalidRelationship", "Relationship type or target is invalid")
        if source[0] in {"user", "group"}:
            try:
                identifier = UUID(source[1])
            except ValueError:
                raise AdminError(
                    422, "InvalidRelationship", "Directory alias requires UUID"
                ) from None
            model = User if source[0] == "user" else Group
            if not await session.scalar(
                select(model.id).where(
                    model.tenant_id == tenant_id, model.id == identifier, model.deleted_at.is_(None)
                )
            ):
                raise AdminError(422, "InvalidRelationship", "Directory alias source was not found")
        elif source not in keys:
            raise AdminError(422, "InvalidRelationship", "Relationship source was not found")
        public_key = (source, relation, target[0], public_ids[target])
        if public_key in public_children:
            raise AdminError(422, "InvalidRelationship", "Duplicate public child ID within parent")
        public_children.add(public_key)
        if relation in {"children", "root"}:
            if target in parent:
                raise AdminError(422, "InvalidDriveTree", "Drive item has multiple parents")
            parent[target] = source
            if relation == "root":
                if source in roots:
                    raise AdminError(422, "InvalidDriveTree", "Drive has multiple roots")
                roots[source] = target
            else:
                source_payload = incoming[source].payload if source in incoming else None
                folder = (
                    source_payload.get("folder")
                    if source_payload is not None
                    else existing[source].folder
                )
                if folder is None:
                    raise AdminError(422, "InvalidDriveTree", "Children require a folder parent")
            name = (
                incoming[target].payload.get("name")
                if target in incoming
                else existing[target].name
            )
            if not isinstance(name, str) or name in {".", ".."} or any(c in name for c in "/\\"):
                raise AdminError(
                    422, "InvalidDriveTree", "Drive item name must be a path component"
                )
            path_key = (source, name.casefold())
            if path_key in paths:
                raise AdminError(422, "InvalidDriveTree", "Ambiguous sibling name")
            paths.add(path_key)
        if relation == "versions":
            if target in versions:
                raise AdminError(422, "InvalidRelationship", "Version has multiple parent items")
            versions.add(target)
    drive_public_ids = set()
    for key in keys:
        if key[0] == "driveItem":
            seen, current = set(), key
            while current[0] == "driveItem":
                if current in seen or current not in parent:
                    raise AdminError(422, "InvalidDriveTree", "Drive item is orphaned or cyclic")
                seen.add(current)
                current = parent[current]
            identity = (current, public_ids[key])
            if identity in drive_public_ids:
                raise AdminError(
                    422, "InvalidDriveTree", "Public item IDs must be unique within a drive"
                )
            drive_public_ids.add(identity)
    blobs = {
        identifier: await object_row(session, tenant_id, identifier, lock=True, finalized=True)
        for identifier in sorted({r.content_id for r in batch.records if r.content_id})
    }
    for record in batch.records:
        if record.content_id and record.payload.get("size") != blobs[record.content_id].size:
            raise AdminError(422, "ContentSizeMismatch", "Payload size must match stored bytes")
    state.revision += 1
    revision = state.revision
    for edge in close:
        edge.valid_to = revision
    for key in incoming.keys() | deleted:
        await session.execute(
            update(V)
            .where(
                V.tenant_id == tenant_id,
                V.workload == workload,
                V.scope == batch.scope,
                V.kind == key[0],
                V.id == key[1],
                V.valid_to.is_(None),
            )
            .values(valid_to=revision)
        )
    await session.flush()
    for key in incoming.keys() | deleted:
        record = incoming.get(key)
        row = V(
            tenant_id=tenant_id,
            workload=workload,
            scope=batch.scope,
            kind=key[0],
            id=key[1],
            valid_from=revision,
            deleted=key in deleted,
            payload=copy.deepcopy(record.payload) if record else {},
            content_id=record.content_id if record else None,
            name=(record.payload.get("name") or record.payload.get("displayName"))
            if record
            else None,
        )
        session.add(row)
        if row.content_id:
            await pin_content(
                session,
                tenant_id,
                row.content_id,
                "workload-version",
                pin_owner(workload, batch.scope, row.kind, row.id, revision),
            )
    for source, relation, target in new_edges:
        session.add(
            E(
                tenant_id=tenant_id,
                workload=workload,
                scope=batch.scope,
                source_kind=source[0],
                source_id=source[1],
                relation=relation,
                target_kind=target[0],
                target_id=target[1],
                valid_from=revision,
            )
        )
    await session.flush()
    return ImportResult(
        revision=revision, count=len(batch.records) + len(batch.edges) + len(batch.tombstones)
    )
