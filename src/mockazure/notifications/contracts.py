"""Operation semantics refine the pinned CSDL's changeNotification union."""

from mockazure.providers.graph.schema_reference import validate_payload


def validate_notification(value):
    if not isinstance(value, dict):
        raise ValueError("Notification object required")
    required = {"subscriptionId", "subscriptionExpirationDateTime", "tenantId"}
    if not required <= value.keys():
        raise ValueError("Required notification property is missing")
    lifecycle = "lifecycleEvent" in value
    if lifecycle == ("changeType" in value):
        raise ValueError("Exactly one notification discriminant is required")
    # resourceData is an open CSDL type. Only basic identity metadata is emitted.
    metadata = value.get("resourceData")
    if lifecycle:
        if (
            value["lifecycleEvent"]
            not in {"reauthorizationRequired", "missed", "subscriptionRemoved"}
            or "resourceData" in value
        ):
            raise ValueError("Invalid lifecycle notification")
    else:
        if value["changeType"] not in {"created", "updated", "deleted"} or not isinstance(
            value.get("resource"), str
        ):
            raise ValueError("Invalid change notification")
        if not isinstance(metadata, dict) or set(metadata) != {"@odata.type", "@odata.id", "id"}:
            raise ValueError("Basic resource identity required")
        if any(not isinstance(v, str) or not v for v in metadata.values()):
            raise ValueError("Invalid resource identity")
        if metadata["@odata.type"] not in {
            "#microsoft.graph.message",
            "#microsoft.graph.event",
            "#microsoft.graph.driveItem",
        }:
            raise ValueError("Unsupported notification resource")
        if metadata["@odata.id"] != value["resource"]:
            raise ValueError("Resource identity mismatch")
    if (
        value.keys()
        - required
        - {"clientState", "changeType", "lifecycleEvent", "resource", "resourceData"}
    ):
        raise ValueError("Unsupported basic notification field")
    # CSDL marks changeType nonnullable; the resource documentation explicitly
    # makes it mutually exclusive with lifecycleEvent. Validate actual fields,
    # then impose operation-specific required properties above.
    validate_payload(
        "v1.0",
        "changeNotification",
        {k: v for k, v in value.items() if k != "resourceData"},
        partial=True,
    )
