"""Pure, explicit source-kind projection; no network or mutable directory lookups."""

from uuid import UUID, uuid5

from mockazure.evidence.repository import validate_facts
from mockazure.evidence.schemas import Projection, ProjectionBatch, SourceEnvelope


def project_source(source: SourceEnvelope) -> ProjectionBatch:
    validate_facts(source)
    if source.ref.kind == "authentication":
        from mockazure.evidence.authentication import project_authentication

        return project_authentication(source)
    if source.ref.kind == "directory":
        return project_directory(source)
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


def project_directory(source: SourceEnvelope) -> ProjectionBatch:
    from mockazure.evidence.directory_facts import validate_directory_facts

    facts = validate_directory_facts(source.facts)
    if facts.operation == "unknown":
        return ProjectionBatch(
            source=source.ref,
            generation=source.generation,
            items=(),
            receipt_status="legacy_incomplete",
        )
    suppressed = facts.operation == "import" or (
        facts.operation == "update" and not facts.changed_fields
    )
    if suppressed:
        return ProjectionBatch(
            source=source.ref, generation=source.generation, items=(), receipt_status="suppressed"
        )
    names = {
        ("user", "create"): "Add user",
        ("user", "update"): "Update user",
        ("user", "delete"): "Hard Delete user",
        ("user", "license"): "Change user license",
        ("group", "create"): "Add group",
        ("group", "update"): "Update group",
        ("group", "delete"): "Delete group",
        ("group", "add_member"): "Add member to group",
        ("group", "remove_member"): "Remove member from group",
    }
    activity = names[(facts.object_kind, facts.operation)]
    if (
        facts.object_kind == "user"
        and facts.operation == "update"
        and (facts.changed_fields == ["accountEnabled"])
    ):
        enabled = facts.modified_properties["accountEnabled"]["after"]
        activity = "Enable account" if enabled else "Disable account"
    identifier = uuid5(source.ref.id, "mockazure:m3b:directoryAudit:0")
    payload = {
        "id": str(identifier),
        "activityDateTime": source.occurred_at.isoformat(),
        "activityDisplayName": activity,
        "category": "UserManagement" if facts.object_kind == "user" else "GroupManagement",
        "correlationId": str(facts.correlation_id),
        "initiatedBy": facts.initiated_by,
        "targetResources": facts.targets,
        "result": "success",
        "loggedByService": "Core Directory",
    }
    return ProjectionBatch(
        source=source.ref,
        generation=source.generation,
        receipt_status="legacy_incomplete" if facts.legacy_incomplete else "projected",
        items=(
            Projection(
                family="directoryAudit",
                id=identifier,
                occurred_at=source.occurred_at,
                available_at=source.available_at,
                payload=payload,
                subject_id=None,
                source_ref=source.ref,
            ),
        ),
    )
