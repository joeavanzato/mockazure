"""Explicit v1.0 security read profiles; payload types come from official CSDL."""

import re
from uuid import UUID

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.providers.arm.activity import instant
from mockazure.providers.graph.schema_reference import schema, validate_payload

TYPES = {
    "alerts": "alert",
    "alerts_v2": "security.alert",
    "incidents": "security.incident",
    "secureScores": "secureScore",
    "secureScoreControlProfiles": "secureScoreControlProfile",
}
SECURITY_ROLES = frozenset(
    {
        "Security Reader",
        "Global Reader",
        "Security Operator",
        "Security Administrator",
        "Global Administrator",
    }
)
PERMISSIONS = {
    f: (
        "SecurityAlert"
        if f == "alerts_v2"
        else "SecurityIncident"
        if f == "incidents"
        else "SecurityEvents"
    )
    for f in TYPES
}
MODERN_FIELDS = frozenset(
    {
        "assignedTo",
        "classification",
        "determination",
        "createdDateTime",
        "lastUpdateDateTime",
        "severity",
        "status",
    }
)
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:=-]{0,255}")


def requirement(family):
    p = PERMISSIONS[family]
    permissions = frozenset({p + ".Read.All", p + ".ReadWrite.All"})
    # Modern Defender endpoints require a supported current directory role.
    return OperationRequirement(
        "graph",
        permissions,
        permissions,
        SECURITY_ROLES if family in {"alerts_v2", "incidents"} else frozenset(),
    )


def properties(family):
    return schema("v1.0")["types"][TYPES[family]]["properties"]


def validate_records(family, records, tenant):
    if family not in TYPES or not isinstance(records, list) or not 1 <= len(records) <= 1000:
        raise ValueError("Expected 1..1000 records from a supported collection")
    seen = set()
    result = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Expected Graph resource")
        identifier = record.get("id")
        if (
            not isinstance(identifier, str)
            or not IDENTIFIER.fullmatch(identifier)
            or identifier in seen
        ):
            raise ValueError("Record ID must be unique within a batch and URL-safe")
        seen.add(identifier)
        # Sparse Graph resources are allowed; supplied fields are always deeply typed.
        validate_payload("v1.0", TYPES[family], record, partial=True)
        for field in ("tenantId", "azureTenantId"):
            if record.get(field) is not None and UUID(record[field]) != tenant:
                raise ValueError("Record tenant does not match import tenant")
        times = {
            name: instant(record[name])[1]
            for name, p in properties(family).items()
            if p["type"] == "Edm.DateTimeOffset" and record.get(name) is not None
        }
        result.append((record, times))
    return result
