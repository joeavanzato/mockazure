from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from mockazure.activity.models import ActivityState, Subscription


async def state(session, tenant, *, create=True, lock=False):
    if create:
        await session.execute(
            insert(ActivityState)
            .values(tenant_id=tenant, generation=uuid4(), revision=0, settings={})
            .on_conflict_do_nothing()
        )
    query = select(ActivityState).where(ActivityState.tenant_id == tenant)
    if lock:
        query = query.with_for_update()
    return await session.scalar(query.execution_options(populate_existing=True))


async def change(session, actor, kind, enabled):
    current = await state(session, actor.tenant_id, lock=True)
    row = await session.get(Subscription, (actor.tenant_id, actor.client_id, kind))
    if row is None:
        row = Subscription(
            tenant_id=actor.tenant_id,
            client_id=actor.client_id,
            content_type=kind,
            enabled=enabled,
            start_revision=current.revision,
            generation=uuid4(),
        )
        session.add(row)
    elif row.enabled != enabled:
        row.enabled, row.generation, row.start_revision = enabled, uuid4(), current.revision
    await session.flush()
    return view(row)


def view(row):
    return {
        "contentType": row.content_type,
        "status": "enabled" if row.enabled else "disabled",
        "webhook": None,
    }
