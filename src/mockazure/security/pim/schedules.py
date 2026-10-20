from hashlib import md5
from uuid import UUID

from mockazure.providers.graph.schema_reference import validate_payload


def public_schedule(row, now, *, instance=False):
    result = dict(row.payload)
    if instance:
        label = "Eligibility" if row.kind == "eligibility" else "Assignment"
        result = {
            "id": str(
                UUID(md5((str(row.id) + ":instance").encode(), usedforsecurity=False).hexdigest())
            ),
            "principalId": str(row.principal_id),
            "roleDefinitionId": result["roleDefinitionId"],
            "directoryScopeId": "/",
            "appScopeId": None,
            "memberType": "Direct",
            "startDateTime": row.starts_at.isoformat(),
            "endDateTime": min(row.ends_at, row.revoked_at).isoformat()
            if row.ends_at and row.revoked_at
            else (row.ends_at or row.revoked_at).isoformat()
            if row.ends_at or row.revoked_at
            else None,
            "role" + label + "ScheduleId": str(row.id),
        }
        if row.kind == "assignment":
            result["assignmentType"] = row.payload["assignmentType"]
            result["roleAssignmentOriginId"] = str(row.id)
        name = "unifiedRole" + label + "ScheduleInstance"
    else:
        result["status"] = (
            "Revoked"
            if row.revoked_at
            else "Expired"
            if row.ends_at and row.ends_at <= now
            else "Granted"
            if row.starts_at > now
            else "Provisioned"
        )
        name = (
            "unifiedRole"
            + ("Eligibility" if row.kind == "eligibility" else "Assignment")
            + "Schedule"
        )
    validate_payload("v1.0", name, result)
    return result


def public_request(row, schedule, now):
    result = dict(row.payload)
    if result["status"] == "Granted" and schedule and schedule.starts_at <= now:
        result["status"] = "Provisioned"
        result["completedDateTime"] = schedule.starts_at.isoformat()
    name = (
        "unifiedRole"
        + ("Eligibility" if row.kind == "eligibility" else "Assignment")
        + "ScheduleRequest"
    )
    validate_payload("v1.0", name, result)
    return result
