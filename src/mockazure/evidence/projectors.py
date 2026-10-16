"""Pure, explicit source-kind projection; no network or mutable directory lookups."""

from uuid import UUID

from mockazure.evidence.repository import validate_facts
from mockazure.evidence.schemas import Projection, ProjectionBatch, SourceEnvelope


def project_source(source: SourceEnvelope) -> ProjectionBatch:
    validate_facts(source)
    if source.ref.kind != "historical":
        raise ValueError("Unknown evidence source kind")
    family = source.facts["family"]
    payload = source.facts["payload"]
    subject = payload.get("userId") if family != "riskyUser" else payload["id"]
    return ProjectionBatch(
        source=source.ref,
        generation=source.generation,
        receipt_status="projected",
        items=(
            Projection(
                family=family,
                id=UUID(payload["id"]),
                occurred_at=source.occurred_at,
                available_at=source.available_at,
                payload=payload,
                subject_id=UUID(subject) if subject else None,
                source_ref=source.ref,
            ),
        ),
    )
