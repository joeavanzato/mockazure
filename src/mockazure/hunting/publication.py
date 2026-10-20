"""Publish inside the existing generation/receipt transaction, never on fixture import."""

from mockazure.hunting.projection import VERSION, project_hunting
from mockazure.hunting.repository import ingest_rows
from mockazure.hunting.schemas import HuntingProvenance


async def publish_hunting(session, source, projections):
    # Recompute the trusted map: caller-supplied rows cannot mint native observations.
    if projections != project_hunting(source):
        raise ValueError("Native hunting projection differs from captured source")
    total = 0
    for projection in projections:
        total += await ingest_rows(
            session,
            source.ref.tenant_id,
            projection.table,
            [projection.row],
            [projection.source_id],
            HuntingProvenance(
                kind="native",
                source_ref=f"{source.generation}:{source.ref.kind}:{source.ref.id}",
                generator_version=VERSION,
            ),
            available_at=source.available_at,
            serialize_tenant=False,
        )
    return total
