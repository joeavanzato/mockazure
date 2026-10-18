"""Folder delta over persisted mailbox revisions; no process-local synchronization state."""

from types import SimpleNamespace

from sqlalchemy import and_, literal, or_, select, union_all

from mockazure.mail.message_models import MailMessageVersion
from mockazure.mail.message_queries import CONTRACT, filter_column, message_page
from mockazure.providers.graph.continuations import decode_node
from mockazure.providers.graph.query.compiler import compile_filter


def snapshot(box, revision, name):
    return (
        select(MailMessageVersion)
        .where(
            MailMessageVersion.tenant_id == box.tenant_id,
            MailMessageVersion.user_id == box.user_id,
            MailMessageVersion.valid_from <= revision,
            or_(MailMessageVersion.valid_to.is_(None), MailMessageVersion.valid_to > revision),
        )
        .subquery(name)
    )


async def delta_page(session, box, folder, options, lower, upper, offset):
    if lower is None:
        rows, more, _ = await message_page(session, box, options, upper, offset, folder)
        return [(row, False) for row in rows], more
    before, after = snapshot(box, lower, "before"), snapshot(box, upper, "after")
    same = before.c.immutable_id == after.c.immutable_id
    removal = (
        select(*before.c, literal(True).label("removed"))
        .select_from(before.outerjoin(after, same))
        .where(
            before.c.folder_id == folder,
            before.c.deleted.is_(False),
            or_(
                after.c.immutable_id.is_(None),
                after.c.deleted.is_(True),
                after.c.folder_id != folder,
                and_(literal(not options["immutable"]), after.c.id != before.c.id),
            ),
        )
    )
    changed = (
        select(*after.c, literal(False).label("removed"))
        .select_from(after.outerjoin(before, same))
        .where(after.c.folder_id == folder, after.c.deleted.is_(False), after.c.valid_from > lower)
    )
    filtered = decode_node(options["filter"])
    if filtered:
        # Preserve folder read/unread events outside the received-time filter.
        predicate = compile_filter(
            filtered,
            CONTRACT,
            resolver=lambda field: (
                after.c.payload[field.name].as_string().cast(filter_column(field).type)
            ),
        )
        changed = changed.where(
            or_(
                predicate,
                and_(
                    before.c.immutable_id.is_not(None),
                    before.c.folder_id == folder,
                    before.c.payload["isRead"]
                    .as_boolean()
                    .is_distinct_from(after.c.payload["isRead"].as_boolean()),
                ),
            )
        )
    events = union_all(removal, changed).subquery("mail_delta")
    query = select(events)
    if options["order"]:
        query = query.order_by(
            events.c.payload["receivedDateTime"]
            .as_string()
            .cast(filter_column(CONTRACT.fields["receivedDateTime"]).type)
            .desc()
            .nulls_last()
        )
    query = (
        query.order_by(events.c.immutable_id, events.c.removed.desc())
        .offset(offset)
        .limit(options["top"] + 1)
    )
    rows = (await session.execute(query)).mappings().all()
    return [(SimpleNamespace(**row), row["removed"]) for row in rows[: options["top"]]], len(
        rows
    ) > options["top"]
