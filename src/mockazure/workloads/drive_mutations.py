"""Bounded local file mutations with immutable byte history and current authority."""

import copy
import hashlib
from uuid import uuid4

from sqlalchemy import and_, or_, select, update

from mockazure.content.models import CHUNK_SIZE
from mockazure.content.repository import begin_upload, finalize_upload, put_chunk
from mockazure.evidence.repository import lock_state
from mockazure.providers.graph.errors import GraphError
from mockazure.workloads.drive_write_store import (
    edge,
    history,
    invalid,
    match,
    name_input,
    record,
    require_write,
    sibling,
)
from mockazure.workloads.drives import resolve_drive_item
from mockazure.workloads.facts import record_permission_fact
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import current_record, scope_row


async def context(session, actor, drive_id, item_id):
    await lock_state(session, actor.tenant_id)
    drive = await current_record(session, actor.tenant_id, "sharepoint", "drive", drive_id)
    if drive is None:
        raise GraphError(404, "itemNotFound", "Drive was not found")
    await scope_row(session, actor.tenant_id, "sharepoint", drive.scope, lock=True)
    await require_write(session, actor, drive.scope)
    return await resolve_drive_item(session, actor.tenant_id, drive_id, item_id)


async def parent_for(session, actor, ctx, reference=None):
    item, drive = ctx["row"], ctx["drive"]
    if reference is None:
        identifier = await session.scalar(
            select(V.payload["id"].as_string())
            .join(
                E,
                (
                    (E.tenant_id == V.tenant_id)
                    & (E.workload == V.workload)
                    & (E.scope == V.scope)
                    & (E.source_kind == V.kind)
                    & (E.source_id == V.id)
                ),
            )
            .where(
                V.tenant_id == actor.tenant_id,
                V.workload == "sharepoint",
                V.scope == drive.scope,
                V.kind == "driveItem",
                V.valid_to.is_(None),
                V.deleted.is_(False),
                E.target_kind == "driveItem",
                E.target_id == item.id,
                E.relation == "children",
                E.valid_to.is_(None),
            )
        )
    else:
        if (
            not isinstance(reference, dict)
            or set(reference) - {"id", "driveId"}
            or not isinstance(reference.get("id"), str)
            or reference.get("driveId", drive.payload["id"]) != drive.payload["id"]
        ):
            invalid("Only same-drive parentReference.id is supported")
        identifier = reference["id"]
    if not identifier:
        invalid("Drive root mutation is unsupported")
    result = await resolve_drive_item(session, actor.tenant_id, drive.payload["id"], identifier)
    if "folder" not in result["row"].payload:
        invalid("The destination must be a folder")
    return result["row"]


def only_file(row):
    if "file" not in row.payload:
        invalid("This bounded workflow supports files; folder mutations are unsupported")


async def facts(session, actor, operation, ctx, before, after, now):
    await record_permission_fact(
        session,
        actor,
        "drive." + operation,
        ctx["state"].scope,
        {k: before[k] for k in ("id", "name", "parentReference", "size", "eTag") if k in before},
        {k: after[k] for k in ("id", "name", "parentReference", "size", "eTag") if k in after},
        now,
    )


async def store_bytes(session, tenant, data, media_type):
    upload = await begin_upload(
        session,
        tenant,
        len(data),
        hashlib.sha256(data).hexdigest(),
        media_type,
        max_size=8 * CHUNK_SIZE,
    )
    for n in range(0, len(data), CHUNK_SIZE):
        await put_chunk(session, tenant, upload, n // CHUNK_SIZE, data[n : n + CHUNK_SIZE])
    return await finalize_upload(session, tenant, upload)


async def upload(session, actor, ctx, data, media_type, now, *, name=None, if_match=None):
    state, item, drive = ctx["state"], ctx["row"], ctx["drive"]
    parent = item if name is not None else await parent_for(session, actor, ctx)
    if name is not None:
        name_input(name)
        if "folder" not in parent.payload:
            invalid("Upload destination must be a folder")
        item = await sibling(session, state, parent, name)
    if item is not None:
        only_file(item)
        match(item, if_match)
    elif if_match:
        raise GraphError(412, "preconditionFailed", "No matching item exists")
    state.revision += 1
    blob = await store_bytes(session, actor.tenant_id, data, media_type)
    before = copy.deepcopy(item.payload) if item else {}
    if item:
        await history(session, state, item)
    identifier = item.payload["id"] if item else str(uuid4())
    value = {
        **before,
        "id": identifier,
        "name": name or before["name"],
        "size": blob.size,
        "file": {"mimeType": blob.media_type},
        "parentReference": {"id": parent.payload["id"], "driveId": drive.payload["id"]},
        "createdDateTime": before.get("createdDateTime", now.isoformat()),
        "lastModifiedDateTime": now.isoformat(),
        "eTag": f'"{identifier},{state.revision}"',
        "cTag": f'"c:{identifier},{state.revision}"',
    }
    result = await record(session, state, "driveItem", value, previous=item, content=blob.id)
    if item is None:
        edge(session, state, parent, "children", result)
    await facts(session, actor, "upload", ctx, before, value, now)
    return value, 200 if item else 201


async def change(session, actor, ctx, operation, body, now, *, if_match=None):
    state, item = ctx["state"], ctx["row"]
    only_file(item)
    match(item, if_match)
    before = copy.deepcopy(item.payload)
    allowed = {"name", "parentReference"} if operation in {"update", "copy"} else set()
    if not isinstance(body, dict) or set(body) - allowed:
        invalid("Unsupported file action property")
    if operation == "update" and not body:
        invalid("Specify name or parentReference")
    # Imported records may express their parent only through authoritative edges.
    # Snapshot that parent before changing edges so audit replay never mistakes
    # response normalization for a move.
    original_parent = await parent_for(session, actor, ctx)
    before["parentReference"] = {
        "id": original_parent.payload["id"],
        "driveId": ctx["drive"].payload["id"],
    }
    parent = (
        await parent_for(session, actor, ctx, body["parentReference"])
        if body.get("parentReference") is not None
        else original_parent
    )
    name = name_input(body.get("name", before["name"]))
    old = await sibling(session, state, parent, name)
    if operation != "delete" and old is not None and (operation == "copy" or old.id != item.id):
        raise GraphError(409, "nameAlreadyExists", "A destination item already has this name")
    state.revision += 1
    if operation == "delete":
        await record(session, state, "driveItem", {}, previous=item, deleted=True)
        await session.execute(
            update(E)
            .where(
                E.tenant_id == actor.tenant_id,
                E.workload == "sharepoint",
                E.scope == state.scope,
                or_(
                    and_(E.target_kind == item.kind, E.target_id == item.id),
                    and_(E.source_kind == item.kind, E.source_id == item.id),
                ),
                E.valid_to.is_(None),
            )
            .values(valid_to=state.revision)
        )
        value = {}
    else:
        identifier = str(uuid4()) if operation == "copy" else item.payload["id"]
        value = {
            **(
                {k: before[k] for k in ("size", "file") if k in before}
                if operation == "copy"
                else before
            ),
            "id": identifier,
            "name": name,
            "parentReference": {"id": parent.payload["id"], "driveId": ctx["drive"].payload["id"]},
            "lastModifiedDateTime": now.isoformat(),
            "eTag": f'"{identifier},{state.revision}"',
        }
        if operation == "copy":
            value["createdDateTime"] = now.isoformat()
        result = await record(
            session,
            state,
            "driveItem",
            value,
            previous=None if operation == "copy" else item,
            content=item.content_id,
        )
        if operation == "copy":
            edge(session, state, parent, "children", result)
        elif "parentReference" in body:
            await session.execute(
                update(E)
                .where(
                    E.tenant_id == actor.tenant_id,
                    E.workload == "sharepoint",
                    E.scope == state.scope,
                    E.target_kind == item.kind,
                    E.target_id == item.id,
                    E.relation == "children",
                    E.valid_to.is_(None),
                )
                .values(valid_to=state.revision)
            )
            edge(session, state, parent, "children", result)
    await facts(session, actor, operation, ctx, before if operation != "copy" else {}, value, now)
    return value
