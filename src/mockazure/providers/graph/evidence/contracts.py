"""Bounded endpoint contracts, independently checked against pinned Microsoft CSDL."""

from mockazure.providers.graph.query.contracts import FieldContract, QueryContract, unsupported
from mockazure.providers.graph.schema_reference import schema

PATHS = {
    "signIn": "auditLogs/signIns",
    "directoryAudit": "auditLogs/directoryAudits",
    "riskDetection": "identityProtection/riskDetections",
    "riskyUser": "identityProtection/riskyUsers",
}
DEFAULTS = {"signIn": 1000, "directoryAudit": 100, "riskDetection": 20, "riskyUser": 100}
CAPS = {"signIn": 1000, "directoryAudit": 1000, "riskDetection": 500, "riskyUser": 500}
TIMES = {
    "signIn": "createdDateTime",
    "directoryAudit": "activityDateTime",
    "riskDetection": "detectedDateTime",
    "riskyUser": "riskLastUpdatedDateTime",
}
FIELDS = {
    "signIn": """id createdDateTime userId userDisplayName userPrincipalName appId appDisplayName
servicePrincipalId servicePrincipalName resourceId resourceDisplayName ipAddress clientAppUsed
correlationId isInteractive status conditionalAccessStatus appliedConditionalAccessPolicies
riskDetail riskLevelAggregated
riskLevelDuringSignIn riskState riskEventTypes_v2 deviceDetail location""".split(),
    "directoryAudit": """id activityDateTime activityDisplayName category correlationId
loggedByService
result resultReason operationType initiatedBy targetResources additionalDetails""".split(),
    "riskyUser": """id userDisplayName userPrincipalName isDeleted isProcessing riskLevel riskState
riskDetail riskLastUpdatedDateTime""".split(),
    "riskDetection": """id userId userDisplayName userPrincipalName riskEventType
riskLevel riskState
riskDetail activity activityDateTime detectedDateTime lastUpdatedDateTime detectionTimingType
source ipAddress location requestId correlationId additionalInfo tokenIssuerType""".split(),
}
BETA_SIGNIN = {
    "signInEventTypes",
    "authenticationProtocol",
    "authenticationRequirement",
    "authenticationDetails",
}


def public_fields(version, family):
    if family == "auditLogRecord":
        return set(schema(version)["types"]["security.auditLogRecord"]["properties"])
    if version not in {"v1.0", "beta"} or family not in FIELDS:
        unsupported("Unsupported evidence resource/version")
    result = set(FIELDS[family])
    if family == "signIn" and version == "beta":
        result |= BETA_SIGNIN
    # Fail startup/development if the locally declared shape drifts from actual metadata.
    if not result <= schema(version)["types"][family]["properties"].keys():
        raise ValueError("Evidence fields differ from the pinned Microsoft schema")
    return result


def evidence_contract(version, resource):
    fields = {
        name: FieldContract(name, "string", frozenset())
        for name in public_fields(version, resource)
    }

    def add(names, kind, operators):
        for name in names.split():
            fields[name] = FieldContract(name, kind, frozenset(operators.split()))

    if resource == "signIn":
        add(
            "id userId appId servicePrincipalId resourceId correlationId userDisplayName "
            "userPrincipalName appDisplayName servicePrincipalName resourceDisplayName ipAddress "
            "clientAppUsed riskDetail riskLevelAggregated riskLevelDuringSignIn riskState",
            "string",
            "eq",
        )
        add("createdDateTime", "datetime", "eq ge gt le lt")
        add("status/errorCode", "int", "eq")
        add("isInteractive", "bool", "eq")
        for name in (
            "userPrincipalName",
            "userDisplayName",
            "appDisplayName",
            "servicePrincipalName",
            "resourceDisplayName",
            "ipAddress",
        ):
            fields[name] = FieldContract(name, "string", frozenset({"eq", "startswith"}))
        if version == "beta":
            add("signInEventTypes", "array_eq", "any")
    elif resource == "directoryAudit":
        add(
            "id category result activityDisplayName correlationId initiatedBy/user/id "
            "initiatedBy/app/appId",
            "string",
            "eq",
        )
        add("activityDateTime", "datetime", "eq ge gt le lt")
        add("activityDisplayName initiatedBy/user/userPrincipalName", "string", "eq startswith")
        add("targetResources", "structured_array", "any")
    elif resource == "riskyUser":
        add("id riskLevel riskState riskDetail", "string", "eq ne")
        add("isDeleted isProcessing", "bool", "eq ne")
        add("riskLastUpdatedDateTime", "datetime", "eq ge gt le lt")
    else:
        add("id userId riskEventType riskLevel riskState riskDetail activity", "string", "eq ne")
        add("activityDateTime detectedDateTime lastUpdatedDateTime", "datetime", "eq ge gt le lt")
    orderable = (
        frozenset({TIMES[resource]}) if resource in {"signIn", "directoryAudit"} else frozenset()
    )
    return QueryContract(version, resource, resource, fields, orderable)
