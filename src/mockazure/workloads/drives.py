"""Canonical drive identity/path resolution and expiring local byte capabilities."""

import hashlib
import secrets
from datetime import timedelta

from sqlalchemy import and_, func, select

from mockazure.content.repository import object_row, pin_content
from mockazure.providers.graph.errors import GraphError
from mockazure.workloads.models import WorkloadDownload
from mockazure.workloads.models import WorkloadRelation as E
from mockazure.workloads.models import WorkloadVersion as V
from mockazure.workloads.repository import current_record, scope_row, visible


def drive_tree(tenant, scope, drive_id, revision, name="drive_tree"):
    base = select(E.target_id.label("id")).where(
        E.tenant_id == tenant,
        E.workload == "sharepoint",
        E.scope == scope,
        E.source_kind == "drive",
        E.source_id == drive_id,
        E.relation == "root",
        visible(E, revision),
    )
    tree = base.cte(name, recursive=True)
    return tree.union_all(
        select(E.target_id)
        .join(tree, tree.c.id == E.source_id)
        .where(
            E.tenant_id == tenant,
            E.workload == "sharepoint",
            E.scope == scope,
            E.source_kind == "driveItem",
            E.relation == "children",
            visible(E, revision),
        )
    )


async def resolve_drive_item(session, tenant_id, drive_id, identifier, by_path=False):
    drive = await current_record(session, tenant_id, "sharepoint", "drive", drive_id)
    if drive is None:
        raise GraphError(404, "itemNotFound", "Drive was not found")
    state = await scope_row(session, tenant_id, "sharepoint", drive.scope)
    root_id = await session.scalar(
        select(E.target_id).where(
            E.tenant_id == tenant_id,
            E.workload == "sharepoint",
            E.scope == drive.scope,
            E.source_kind == "drive",
            E.source_id == drive_id,
            E.relation == "root",
            E.valid_to.is_(None),
        )
    )
    if root_id is None:
        raise GraphError(404, "itemNotFound", "Drive root was not imported")
    if by_path:
        current = root_id
        pieces = identifier.split("/") if identifier else []
        if any(part in {"", ".", ".."} or "\\" in part for part in pieces):
            raise GraphError(400, "invalidRequest", "Invalid drive path")
        for name in pieces:
            current = await session.scalar(
                select(V.id)
                .join(
                    E,
                    and_(
                        E.target_id == V.id,
                        E.target_kind == V.kind,
                        E.tenant_id == V.tenant_id,
                        E.scope == V.scope,
                        E.workload == V.workload,
                    ),
                )
                .where(
                    V.tenant_id == tenant_id,
                    V.workload == "sharepoint",
                    V.scope == drive.scope,
                    V.deleted.is_(False),
                    V.valid_to.is_(None),
                    E.valid_to.is_(None),
                    E.source_kind == "driveItem",
                    E.source_id == current,
                    E.relation == "children",
                    func.lower(V.name) == name.lower(),
                )
            )
            if current is None:
                raise GraphError(404, "itemNotFound", "Drive path was not found")
        identifier = current
    elif identifier == "root":
        identifier = root_id
    tree = drive_tree(tenant_id, drive.scope, drive_id, state.revision)
    row = await session.scalar(
        select(V).where(
            V.tenant_id == tenant_id,
            V.workload == "sharepoint",
            V.scope == drive.scope,
            V.kind == "driveItem",
            V.id == identifier,
            V.valid_to.is_(None),
            V.deleted.is_(False),
            V.id.in_(select(tree.c.id)),
        )
    )
    if row is None:
        raise GraphError(404, "itemNotFound", "Item was not found in this drive")
    return {"row": row, "drive": drive, "state": state}


async def issue_download(session, context, content_id, now):
    await object_row(session, context.tenant_id, content_id, lock=True, finalized=True)
    token = secrets.token_hex(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    session.add(
        WorkloadDownload(
            token_hash=digest,
            tenant_id=context.tenant_id,
            content_id=content_id,
            expires_at=now + timedelta(minutes=5),
        )
    )
    await pin_content(session, context.tenant_id, content_id, "workload-download", digest)
    await session.flush()
    return token


async def verify_download(session, token, now):
    if len(token) != 64 or not token.isascii():
        return None
    row = await session.get(WorkloadDownload, hashlib.sha256(token.encode()).hexdigest())
    return row if row is not None and row.expires_at > now else None
