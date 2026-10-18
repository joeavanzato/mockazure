"""Portable typed manifests, streamed archives and scope-preserving reset."""

import json
import zipfile
from collections import deque
from uuid import uuid4

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import aliased

from mockazure.admin.errors import AdminError
from mockazure.content.models import ContentObject
from mockazure.content.repository import object_row, release_content, stream_content
from mockazure.workloads.models import WorkloadGrant as G
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import pin_owner, scope_row


def content_query(tenant, workload, scope):
    return (
        select(ContentObject)
        .join(
            V,
            and_(
                V.tenant_id == ContentObject.tenant_id,
                V.content_id == ContentObject.id,
            ),
        )
        .where(
            V.tenant_id == tenant,
            V.workload == workload,
            V.scope == scope,
            V.valid_to.is_(None),
            V.deleted.is_(False),
        )
        .distinct()
        .order_by(ContentObject.id)
    )


async def content_descriptors(session, tenant, workload, scope):
    rows = await session.stream_scalars(
        content_query(tenant, workload, scope).execution_options(yield_per=1)
    )
    try:
        async for blob in rows:
            yield {
                "id": str(blob.id),
                "size": blob.size,
                "sha256": blob.sha256,
                "media_type": blob.media_type,
            }
    finally:
        await rows.close()


async def restored_records(session, tenant, workload, batch):
    manifest = batch.manifest
    if (manifest.tenant_id, manifest.workload, manifest.scope) != (tenant, workload, batch.scope):
        raise AdminError(
            422, "ArchiveIdentityMismatch", "Archive tenant/workload/scope does not match"
        )
    descriptors = {value.id: value for value in manifest.content}
    needed = {row.content_id for row in batch.records if row.content_id is not None}
    if len(descriptors) != len(manifest.content) or needed - descriptors.keys():
        raise AdminError(422, "InvalidManifest", "Duplicate or missing binary descriptors")
    mapping = {}
    for identifier, value in descriptors.items():
        target = await session.scalar(
            select(ContentObject.id).where(
                ContentObject.tenant_id == tenant,
                ContentObject.state == "finalized",
                ContentObject.size == value.size,
                ContentObject.sha256 == value.sha256,
                ContentObject.media_type == value.media_type,
            )
        )
        if target is None:
            raise AdminError(
                422, "ManifestMismatch", "Upload and finalize the matching archive bytes first"
            )
        mapping[identifier] = target
    # Import subsequently locks all resolved objects in canonical UUID order before pinning.
    return [
        row.model_copy(update={"content_id": mapping[row.content_id]})
        if row.content_id is not None
        else row
        for row in batch.records
    ]


async def export_records(session, tenant, workload, scope):
    state = await scope_row(session, tenant, workload, scope, lock="read")
    if state is None:
        raise AdminError(404, "ScopeNotFound", "Workload scope was not found")
    yield {
        "type": "scope",
        "value": {
            "scope": scope,
            "expected_revision": state.revision,
            "manifest": {
                "version": 1,
                "tenant_id": str(tenant),
                "workload": workload,
                "scope": scope,
                "content": [],
            },
        },
    }
    async for descriptor in content_descriptors(session, tenant, workload, scope):
        yield {"type": "content", "value": descriptor}
    records = await session.stream_scalars(
        select(V)
        .where(
            V.tenant_id == tenant,
            V.workload == workload,
            V.scope == scope,
            V.valid_to.is_(None),
            V.deleted.is_(False),
        )
        .order_by(V.kind, V.id)
        .execution_options(yield_per=1)
    )
    try:
        async for row in records:
            yield {
                "type": "record",
                "value": {
                    "kind": row.kind,
                    "id": row.payload["id"],
                    **({"key": row.id} if row.id != row.payload["id"] else {}),
                    "payload": row.payload,
                    "content_id": str(row.content_id) if row.content_id else None,
                },
            }
    finally:
        await records.close()
    source, target = aliased(V), aliased(V)

    def join_clause(model, side):
        return and_(
            model.tenant_id == E.tenant_id,
            model.workload == E.workload,
            model.scope == E.scope,
            model.kind == getattr(E, side + "_kind"),
            model.id == getattr(E, side + "_id"),
            model.valid_to.is_(None),
            model.deleted.is_(False),
        )

    edges = await session.stream(
        select(
            E,
            source.payload["id"].as_string().label("source_public"),
            target.payload["id"].as_string().label("target_public"),
        )
        .outerjoin(source, join_clause(source, "source"))
        .join(target, join_clause(target, "target"))
        .where(
            E.tenant_id == tenant, E.workload == workload, E.scope == scope, E.valid_to.is_(None)
        )
        .order_by(E.source_kind, E.source_id, E.relation, E.target_id)
        .execution_options(yield_per=1)
    )
    try:
        async for edge, source_id, target_id in edges:
            source_id = (
                source_id or edge.source_id
            )  # External directory alias keys are public UUIDs.
            yield {
                "type": "edge",
                "value": {
                    "source": {
                        "kind": edge.source_kind,
                        "id": source_id,
                        **({"key": edge.source_id} if edge.source_id != source_id else {}),
                    },
                    "relation": edge.relation,
                    "target": {
                        "kind": edge.target_kind,
                        "id": target_id,
                        **({"key": edge.target_id} if edge.target_id != target_id else {}),
                    },
                },
            }
    finally:
        await edges.close()


async def export_workload(session, tenant, workload, scope):
    result = {"records": [], "edges": [], "tombstones": []}
    size, count = 256, 0
    async for row in export_records(session, tenant, workload, scope):
        size += len(json.dumps(row, ensure_ascii=False).encode("utf-8"))
        count += row["type"] in {"record", "edge"}
        if count > 1000 or size > 8 * 1024 * 1024:
            raise AdminError(
                413, "UseStreamingExport", "Use format=ndjson or zip for bounded streaming"
            )
        if row["type"] == "scope":
            result.update(row["value"])
        elif row["type"] == "content":
            result["manifest"]["content"].append(row["value"])
        else:
            result[row["type"] + "s"].append(row["value"])
    return result


async def check_archive_size(session, tenant, workload, scope):
    count = await session.scalar(
        select(func.count()).select_from(content_query(tenant, workload, scope).subquery())
    )
    if count > 10000:
        raise AdminError(413, "ArchiveTooLarge", "Archive contains more than10000 binary entries")


class ArchiveBuffer:
    def __init__(self):
        self.offset, self.chunks = 0, deque()

    def write(self, data):
        if data:
            self.chunks.append(bytes(data))
            self.offset += len(data)
        return len(data)

    def tell(self):
        return self.offset

    def flush(self):
        pass

    def drain(self):
        while self.chunks:
            yield self.chunks.popleft()


async def export_archive(session, tenant, workload, scope):
    buffer = ArchiveBuffer()
    # Non-seekable ZIP64 with stored entries: one content chunk plus bounded metadata at a time.
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        with archive.open("manifest.ndjson", "w", force_zip64=True) as target:
            async for row in export_records(session, tenant, workload, scope):
                target.write(json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n")
                for chunk in buffer.drain():
                    yield chunk
        for chunk in buffer.drain():
            yield chunk
        # Separate descriptor and byte streams share the same scope lease.
        async for descriptor in content_descriptors(session, tenant, workload, scope):
            from uuid import UUID

            content_id = UUID(descriptor["id"])
            await object_row(session, tenant, content_id, lock="read", finalized=True)
            with archive.open(
                "content/" + descriptor["id"] + ".bin", "w", force_zip64=True
            ) as target:
                async for chunk in stream_content(
                    session, tenant, content_id, 0, descriptor["size"]
                ):
                    target.write(chunk)
                    for data in buffer.drain():
                        yield data
            for data in buffer.drain():
                yield data
    for data in buffer.drain():
        yield data


async def reset_workload(session, tenant, workload, scope, expected_revision):
    state = await scope_row(session, tenant, workload, scope, lock=True)
    if state is None:
        raise AdminError(404, "ScopeNotFound", "Workload scope was not found")
    if state.revision != expected_revision:
        raise AdminError(409, "WorkloadRevisionConflict", "Workload revision changed")
    records = await session.stream_scalars(
        select(V)
        .where(V.tenant_id == tenant, V.workload == workload, V.scope == scope)
        .execution_options(yield_per=1)
    )
    async for row in records:
        if row.content_id:
            await release_content(
                session,
                tenant,
                "workload-version",
                pin_owner(workload, scope, row.kind, row.id, row.valid_from),
            )
    for model in (G, E, V):
        await session.execute(
            delete(model).where(
                model.tenant_id == tenant, model.workload == workload, model.scope == scope
            )
        )
    # Keep invalidated handles until expiry so resumption reports410 after fixture restoration.
    state.revision, state.generation = 0, uuid4()
    state.grant_revision += 1
    await session.flush()
    return {"scope": scope, "revision": 0}
