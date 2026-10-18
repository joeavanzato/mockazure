"""Atomic mail fixture imports and versioned message storage."""

import base64
import copy
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select, update

from mockazure.admin.errors import AdminError
from mockazure.content.repository import object_row, pin_content
from mockazure.mail.content_models import WELL_KNOWN, ImportResult, MailContentImport
from mockazure.mail.message_models import (
    MailboxContentState,
    MailContentFolder,
    MailMessage,
    MailMessageVersion,
)
from mockazure.mail.models import MailboxFolder, MailRule
from mockazure.mail.repository import resolve_mailbox
from mockazure.providers.graph.errors import GraphError


def immutable_identifier(identifier):
    return "AAIk" + base64.urlsafe_b64encode(identifier.bytes).decode().rstrip("=")


def immutable_key(value):
    if not isinstance(value, str) or not value.startswith("AAIk"):
        return None
    try:
        raw = base64.urlsafe_b64decode(value[4:] + "==")
        key = UUID(bytes=raw)
        return key if immutable_identifier(key) == value else None
    except (ValueError, TypeError):
        return None


async def message_by_id(session, box, identifier, *, required=True):
    key = immutable_key(identifier)
    predicate = MailMessage.immutable_id == key if key else MailMessage.id == identifier
    row = await session.scalar(
        select(MailMessage).where(
            MailMessage.tenant_id == box.tenant_id, MailMessage.user_id == box.user_id, predicate
        )
    )
    if row is None and required:
        raise GraphError(404, "ErrorItemNotFound", "Message was not found")
    return row


async def folder_rows(session, box):
    folders = list(
        await session.scalars(
            select(MailboxFolder)
            .where(
                MailboxFolder.tenant_id == box.tenant_id,
                MailboxFolder.user_id == box.user_id,
                MailboxFolder.archive.is_(False),
            )
            .order_by(MailboxFolder.id)
        )
    )
    metadata = {
        row.id: row
        for row in await session.scalars(
            select(MailContentFolder).where(
                MailContentFolder.tenant_id == box.tenant_id,
                MailContentFolder.user_id == box.user_id,
            )
        )
    }
    return folders, metadata


async def folder_by_id(session, box, identifier):
    folders, metadata = await folder_rows(session, box)
    for folder in folders:
        if folder.id == identifier:
            return folder
    for folder in folders:
        alias = (
            metadata[folder.id].well_known_name
            if folder.id in metadata
            else folder.id.casefold()
            if folder.id.casefold() in WELL_KNOWN
            else None
        )
        if alias == identifier.casefold():
            return folder
    raise GraphError(404, "ErrorItemNotFound", "Primary mailbox folder was not found")


async def state_for(session, box, *, create=False):
    state = await session.get(MailboxContentState, (box.tenant_id, box.user_id))
    if state is None and create:
        state = MailboxContentState(
            tenant_id=box.tenant_id, user_id=box.user_id, revision=0, generation=uuid4()
        )
        session.add(state)
        await session.flush()
    return state


def owner_for(row, revision):
    return f"{row.user_id}:{row.immutable_id}:{revision}"


async def save_version(session, row, revision, *, deleted=False):
    await session.execute(
        update(MailMessageVersion)
        .where(
            MailMessageVersion.tenant_id == row.tenant_id,
            MailMessageVersion.user_id == row.user_id,
            MailMessageVersion.immutable_id == row.immutable_id,
            MailMessageVersion.valid_to.is_(None),
        )
        .values(valid_to=revision)
    )
    record = MailMessageVersion(
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        immutable_id=row.immutable_id,
        valid_from=revision,
        id=row.id,
        folder_id=row.folder_id,
        deleted=deleted,
        payload={} if deleted else copy.deepcopy(row.payload),
        mime_content_id=None if deleted else row.mime_content_id,
        size_bytes=0 if deleted else row.size_bytes,
        attachments=[] if deleted else copy.deepcopy(row.attachments),
    )
    session.add(record)
    if not deleted:
        ids = {UUID(v["content_id"]) for v in row.attachments if v.get("content_id")}
        if row.mime_content_id:
            ids.add(row.mime_content_id)
        for content_id in sorted(ids):
            await pin_content(
                session, row.tenant_id, content_id, "mail-version", owner_for(row, revision)
            )
    await session.flush()


async def recount(session, box, folders=None):
    query = select(MailboxFolder).where(
        MailboxFolder.tenant_id == box.tenant_id,
        MailboxFolder.user_id == box.user_id,
        MailboxFolder.archive.is_(False),
    )
    if folders is not None:
        query = query.where(MailboxFolder.id.in_(folders))
    for folder in await session.scalars(query):
        count, size = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(MailMessage.size_bytes), 0)).where(
                    MailMessage.tenant_id == box.tenant_id,
                    MailMessage.user_id == box.user_id,
                    MailMessage.folder_id == folder.id,
                )
            )
        ).one()
        folder.item_count, folder.size_bytes = int(count), int(size)
    await session.flush()


async def import_mail_content(session, tenant_id: UUID, batch: MailContentImport) -> ImportResult:
    box = await resolve_mailbox(session, tenant_id, str(batch.mailbox_user_id))
    await session.refresh(box, with_for_update=True)
    state = await state_for(session, box)
    if batch.expected_revision != (state.revision if state else 0):
        raise AdminError(409, "MailRevisionConflict", "Mailbox revision changed")
    existing_folders, metadata = await folder_rows(session, box)
    folders = {row.id: row for row in existing_folders}
    parents = {
        row.id: metadata[row.id].parent_id if row.id in metadata else None
        for row in existing_folders
    }
    parents.update({row.id: row.parentFolderId for row in batch.folders})
    aliases = {key: row.well_known_name for key, row in metadata.items()}
    aliases.update({row.id: row.well_known_name for row in batch.folders})
    names = [name for name in aliases.values() if name]
    if len(set(names)) != len(names):
        raise AdminError(422, "InvalidFolderTree", "Duplicate well-known folder alias")
    for identifier in parents:
        seen, current = set(), identifier
        while current is not None:
            if current in seen or current not in parents:
                raise AdminError(422, "InvalidFolderTree", "Folder parent is missing or cyclic")
            seen.add(current)
            current = parents[current]
    for folder in batch.folders:
        stored = await session.get(MailboxFolder, (tenant_id, box.user_id, folder.id))
        if stored is not None and stored.archive:
            raise AdminError(422, "ArchiveBoundary", "Graph imports cannot replace archive folders")
    for rule in await session.scalars(
        select(MailRule).where(MailRule.tenant_id == tenant_id, MailRule.user_id == box.user_id)
    ):
        for name in ("moveToFolder", "copyToFolder"):
            target = rule.payload.get("actions", {}).get(name)
            if target and target not in parents:
                raise AdminError(
                    422, "RuleFolderMissing", "Existing rule target must remain a primary folder"
                )
    imported = {row.id: row for row in batch.messages}
    affected = set(imported) | set(batch.tombstones) | {row.message_id for row in batch.attachments}
    current = {
        row.id: row
        for row in await session.scalars(
            select(MailMessage).where(
                MailMessage.tenant_id == tenant_id,
                MailMessage.user_id == box.user_id,
                MailMessage.id.in_(affected),
            )
        )
    }
    for row in batch.messages:
        if row.folder_id not in parents:
            raise AdminError(422, "FolderNotFound", "Message parent folder was not found")
        if (
            row.immutable_id
            and row.id in current
            and row.immutable_id != current[row.id].immutable_id
        ):
            raise AdminError(
                422, "ImmutableIdentityConflict", "Existing immutable identity cannot change"
            )
        if row.immutable_id:
            collision = await session.get(MailMessage, (tenant_id, box.user_id, row.immutable_id))
            if collision is not None and collision.id != row.id:
                raise AdminError(
                    422, "ImmutableIdentityConflict", "Immutable message identity is already used"
                )
    if any(
        row.message_id not in imported and row.message_id not in current
        for row in batch.attachments
    ):
        raise AdminError(422, "MessageNotFound", "Attachment parent message was not found")
    if any(identifier not in current for identifier in batch.tombstones):
        raise AdminError(404, "MessageNotFound", "Tombstone message was not found")
    ids = {row.mime_content_id for row in batch.messages if row.mime_content_id} | {
        row.content_id for row in batch.attachments if row.content_id
    }
    # Include retained attachment references when only metadata changes; lock globally sorted IDs.
    for row in current.values():
        if row.mime_content_id:
            ids.add(row.mime_content_id)
        ids.update(UUID(v["content_id"]) for v in row.attachments if v.get("content_id"))
    blobs = {
        identifier: await object_row(session, tenant_id, identifier, lock=True, finalized=True)
        for identifier in sorted(ids)
    }
    for attachment in batch.attachments:
        if (
            attachment.content_id
            and "size" in attachment.payload
            and attachment.payload["size"] != blobs[attachment.content_id].size
        ):
            raise AdminError(
                422, "AttachmentSizeMismatch", "Attachment size disagrees with local bytes"
            )
    state = state or await state_for(session, box, create=True)
    state.revision += 1
    revision = state.revision
    for value in batch.folders:
        row = folders.get(value.id)
        if row is None:
            row = MailboxFolder(
                tenant_id=tenant_id,
                user_id=box.user_id,
                id=value.id,
                name=value.displayName,
                path="/" + value.displayName,
                archive=False,
                item_count=0,
                size_bytes=0,
            )
            session.add(row)
        row.name = value.displayName
        await session.flush()
        meta = metadata.get(value.id)
        if meta is None:
            meta = MailContentFolder(tenant_id=tenant_id, user_id=box.user_id, id=value.id)
            session.add(meta)
        meta.parent_id, meta.is_hidden, meta.well_known_name = (
            value.parentFolderId,
            value.isHidden,
            value.well_known_name,
        )
    await session.flush()
    if batch.mailbox_settings is not None:
        state.settings = copy.deepcopy(batch.mailbox_settings)
    now = datetime.now(UTC).isoformat()
    touched = {}
    for value in batch.messages:
        row = current.get(value.id)
        if row is None:
            row = MailMessage(
                tenant_id=tenant_id,
                user_id=box.user_id,
                immutable_id=value.immutable_id or uuid4(),
                id=value.id,
                attachments=[],
            )
            session.add(row)
        row.folder_id = value.folder_id
        row.mime_content_id = value.mime_content_id
        row.size_bytes = (
            blobs[value.mime_content_id].size if value.mime_content_id else value.size_bytes
        )
        row.payload = {
            "isRead": False,
            "categories": [],
            "createdDateTime": now,
            "lastModifiedDateTime": now,
            **copy.deepcopy(value.payload),
        }
        row.revision = revision
        touched[row.id] = row
    for attachment in batch.attachments:
        row = touched.get(attachment.message_id) or current[attachment.message_id]
        record = attachment.model_dump(mode="json", exclude={"message_id"})
        if attachment.content_id:
            record["payload"]["size"] = blobs[attachment.content_id].size
        row.attachments = [old for old in row.attachments if old["id"] != attachment.id] + [record]
        row.revision = revision
        touched[row.id] = row
    await session.flush()
    for row in touched.values():
        await save_version(session, row, revision)
    for identifier in batch.tombstones:
        row = current[identifier]
        await save_version(session, row, revision, deleted=True)
        await session.delete(row)
    await session.flush()
    await recount(session, box)
    return ImportResult(
        revision=revision,
        count=sum(
            len(v) for v in (batch.folders, batch.messages, batch.attachments, batch.tombstones)
        ),
    )


async def mail_export_records(session, tenant_id, user_id):
    box = await resolve_mailbox(session, tenant_id, str(user_id))
    state = await state_for(session, box)
    if state is None:
        raise AdminError(
            409, "StatisticsOnlyMailbox", "Mailbox has not transitioned to content-backed mode"
        )
    yield {
        "type": "mailbox",
        "value": {
            "mailbox_user_id": str(user_id),
            "mode": "content-backed",
            "expected_revision": state.revision,
            "mailbox_settings": state.settings,
        },
    }
    folders, metadata = await folder_rows(session, box)
    for folder in folders:
        meta = metadata.get(folder.id)
        yield {
            "type": "folder",
            "value": {
                "id": folder.id,
                "displayName": folder.name,
                "parentFolderId": meta.parent_id if meta else None,
                "isHidden": bool(meta and meta.is_hidden),
                "well_known_name": meta.well_known_name if meta else None,
            },
        }
    rows = await session.stream_scalars(
        select(MailMessage)
        .where(MailMessage.tenant_id == tenant_id, MailMessage.user_id == user_id)
        .order_by(MailMessage.id)
        .execution_options(yield_per=1)
    )
    async for row in rows:
        yield {
            "type": "message",
            "value": {
                "id": row.id,
                "immutable_id": str(row.immutable_id),
                "folder_id": row.folder_id,
                "mime_content_id": str(row.mime_content_id) if row.mime_content_id else None,
                "size_bytes": row.size_bytes,
                "payload": row.payload,
            },
        }
        for value in row.attachments:
            yield {"type": "attachment", "value": {"message_id": row.id, **value}}


async def export_mail_content(session, tenant_id, user_id):
    import json

    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    result = {"folders": [], "messages": [], "attachments": [], "tombstones": []}
    count, size = 0, 256
    async for record in mail_export_records(session, tenant_id, user_id):
        size += len(json.dumps(record, ensure_ascii=False).encode("utf-8"))
        count += record["type"] != "mailbox"
        if count > 1000 or size > 8 * 1024 * 1024:
            raise AdminError(
                413, "UseStreamingExport", "Use format=ndjson and restore in bounded import batches"
            )
        if record["type"] == "mailbox":
            result.update(record["value"])
        else:
            result[record["type"] + "s"].append(record["value"])
    return result


async def reset_mail_content(session, tenant_id, user_id, expected_revision):
    from mockazure.content.models import ContentReference
    from mockazure.mail.message_models import MailContentCursor
    from mockazure.mail.models import Mailbox

    box = await session.get(Mailbox, (tenant_id, user_id), with_for_update=True)
    if box is None:
        raise AdminError(404, "MailboxNotFound", "Mailbox was not found")
    state = await state_for(session, box)
    if expected_revision != (state.revision if state else 0):
        raise AdminError(409, "MailRevisionConflict", "Mailbox revision changed")
    for model in (MailContentCursor, MailMessageVersion, MailMessage, MailContentFolder):
        await session.execute(
            delete(model).where(model.tenant_id == tenant_id, model.user_id == user_id)
        )
    await session.execute(
        delete(ContentReference).where(
            ContentReference.tenant_id == tenant_id,
            ContentReference.owner_kind == "mail-version",
            ContentReference.owner_id.startswith(str(user_id) + ":", autoescape=True),
        )
    )
    if state:
        await session.delete(state)
    await recount(session, box)
    return {"mode": "statistics-only", "revision": 0}
