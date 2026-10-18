"""Pinned common Management Activity schema; workload extensions remain public JSON."""

import copy
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from mockazure.mail.audit_schemas import public_audit_data, validate_audit_record

CONTENT_TYPES = frozenset(
    {"Audit.AzureActiveDirectory", "Audit.Exchange", "Audit.SharePoint", "Audit.General", "DLP.All"}
)
AUDIENCE = "https://manage.office.com"
ENUMS = json.loads(Path(__file__).with_name("enum_values.json").read_text(encoding="utf-8"))
RECORD_TYPES = ENUMS["auditLogRecordType"]
USER_TYPES = ENUMS["auditLogUserType"]
MAX_RECORD_BYTES = 1024 * 1024


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|\+00:00)?)?",
        value,
    ):
        raise ValueError("A UTC date or timestamp is required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result


def window(start, end, now):
    if start is None and end is None:
        return now - timedelta(days=1), now
    if start is None or end is None:
        raise ValueError("Both window bounds are required")
    start, end = utc(start), utc(end)
    if not now - timedelta(days=7) <= start < end or end - start > timedelta(days=1):
        raise ValueError("Window must be positive, at most 24 hours, and start within seven days")
    return start, end


def validate_record(record, tenant):
    if not isinstance(record, dict):
        raise ValueError("An audit record object is required")
    if len(json.dumps(record, allow_nan=False).encode()) > MAX_RECORD_BYTES:
        raise ValueError("Audit record exceeds the local one MiB limit")
    for name in ("Id", "OrganizationId"):
        if not isinstance(record.get(name), str):
            raise ValueError("Audit identities must be UUID strings")
        UUID(record[name])
    if UUID(record["OrganizationId"]) != tenant:
        raise ValueError("Audit organization does not match tenant")
    utc(record.get("CreationTime"))
    for field in ("Operation", "UserKey", "UserId", "Workload"):
        value = record.get(field)
        if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
            raise ValueError("Required audit string is absent or invalid")
    for field, values in (("RecordType", RECORD_TYPES), ("UserType", USER_TYPES)):
        if (
            type(record.get(field)) is not int
            or record[field] not in values.values()
            or record[field] < 0
        ):
            raise ValueError("Audit enum is outside the pinned schema")
    for field in ("ClientIP", "ObjectId", "ResultStatus"):
        value = record.get(field)
        if value is not None and (not isinstance(value, str) or "\0" in value):
            raise ValueError("Common audit field must be a string or null")
    if "Scope" in record and (type(record["Scope"]) is not int or record["Scope"] not in (0, 1)):
        raise ValueError("Audit scope must be the pinned numeric enum")
    public_audit_data(record)
    return record


def from_graph(payload, tenant):
    validate_audit_record(payload, tenant=tenant)
    data = copy.deepcopy((payload.get("auditData") or {}).get("dynamicProperties") or {})
    if not isinstance(data, dict):
        raise ValueError("Graph auditData must contain an object")
    user = payload.get("userPrincipalName") or payload.get("userId")
    data.update(
        Id=payload["id"],
        OrganizationId=payload["organizationId"],
        CreationTime=payload["createdDateTime"],
        Operation=payload.get("operation"),
        RecordType=RECORD_TYPES.get(payload.get("auditLogRecordType")),
        UserType=USER_TYPES.get(payload.get("userType")),
        Workload=payload.get("service"),
        UserId=user,
        UserKey=data.get("UserKey") or payload.get("userId") or user,
    )
    for original, field in (("objectId", "ObjectId"), ("clientIp", "ClientIP")):
        if original in payload:
            data[field] = payload[original]
    return validate_record(data, tenant)


def content_type(record):
    name = next(k for k, v in RECORD_TYPES.items() if v == record["RecordType"])
    if "dlp" in name.lower():
        return "DLP.All"
    workload = record["Workload"].lower()
    if workload in {"azureactivedirectory", "microsoftentra", "microsoftentraid"}:
        return "Audit.AzureActiveDirectory"
    if workload == "exchange":
        return "Audit.Exchange"
    if workload in {"sharepoint", "onedrive"}:
        return "Audit.SharePoint"
    return "Audit.General"
