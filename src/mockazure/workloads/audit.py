"""Bounded Purview file/Teams events, derived only from versioned native facts."""

from uuid import UUID, uuid5

from mockazure.evidence.operations import MAPPING_VERSION, mapping
from mockazure.evidence.schemas import Projection, ProjectionBatch
from mockazure.mail.audit_schemas import validate_audit_record
from mockazure.workloads.facts import validate_workload_facts


def operation_name(facts):
    key, before, after = facts["operation"], facts["before"], facts["after"]
    if key == "drive.upload":
        return "FileModified" if before else "FileUploaded"
    if key == "drive.update":
        if before.get("parentReference", {}).get("id") != after.get("parentReference", {}).get(
            "id"
        ):
            return "FileMoved"
        return "FileRenamed" if before.get("name") != after.get("name") else None
    if key in {"drive.copy", "drive.delete", "teams.chat.create"}:
        return {
            "drive.copy": "FileCopied",
            "drive.delete": "FileDeleted",
            "teams.chat.create": "ChatCreated",
        }[key]
    if ".member." in key:
        return {"add": "MemberAdded", "delete": "MemberRemoved", "update": "MemberRoleChanged"}.get(
            key.rsplit(".", 1)[-1]
        )
    return {"update": "GraphMessageUpdated", "undosoftdelete": "GraphMessageUndeleted"}.get(
        key.rsplit(".", 1)[-1]
    )


def project(source):
    facts = validate_workload_facts(source.facts)
    empty = ProjectionBatch(
        source=source.ref, generation=source.generation, items=(), receipt_status="suppressed"
    )
    # Previously captured facts keep their original no-projection disposition on replay.
    if facts.get("mapping_version") != MAPPING_VERSION:
        return empty
    if not mapping(facts["operation"]).destinations:
        return empty
    operation = operation_name(facts)
    if operation is None:
        return empty
    file = facts["operation"].startswith("drive.")
    service, record_type, record_number = (
        ("SharePoint", "SharePointFileOperation", 6)
        if file
        else ("MicrosoftTeams", "MicrosoftTeams", 25)
    )
    identifier = uuid5(source.ref.id, "mockazure:workload:auditLogRecord:v1")
    user, app = facts["actor"].get("user"), facts["actor"].get("app")
    actor_id = user["id"] if user else app["servicePrincipalId"]
    actor_name = user.get("userPrincipalName") if user else None
    user_type = "Regular" if user else "ServicePrincipal"
    target = facts["after"] or facts["before"]
    object_id = target.get("webUrl") or target.get("id") or facts["scope"]
    data = {
        "Id": str(identifier),
        "CreationTime": source.occurred_at.isoformat(),
        "RecordType": record_number,
        "Operation": operation,
        "OrganizationId": str(source.ref.tenant_id),
        "UserType": 0 if user else 6,
        "UserKey": actor_id,
        "UserId": actor_name or actor_id,
        "Workload": service,
        "ResultStatus": "Succeeded",
        "ObjectId": object_id,
    }
    if app:
        data["AppId"] = app["appId"]
    if file:
        data.update(ItemType="File", SourceFileName=target.get("name", ""))
    else:
        if ".chat." not in facts["operation"]:
            try:
                data["TeamGuid"] = str(UUID(facts["scope"]))
            except ValueError:
                pass  # Imported opaque fixture IDs are not valid Edm.Guid values.
        if ".member." in facts["operation"]:
            member = {
                "Role": 1 if "owner" in target.get("roles", []) else 0,
            }
            if target.get("email"):
                member["UPN"] = target["email"]
            if target.get("displayName"):
                member["DisplayName"] = target["displayName"]
            data["Members"] = [member]
        if ".message." in facts["operation"]:
            data["MessageId"] = target["id"]
            message = {"Id": target["id"]}
            if ".chat." in facts["operation"]:
                message["ChatThreadId"] = facts["scope"]
            elif "TeamGuid" in data:
                message["TeamGuid"] = data["TeamGuid"]
            if target.get("replyToId"):
                message["ParentMessageId"] = target["replyToId"]
            data["Messages"] = [message]
    payload = {
        "id": str(identifier),
        "createdDateTime": source.occurred_at.isoformat(),
        "auditLogRecordType": record_type,
        "operation": operation,
        "organizationId": str(source.ref.tenant_id),
        "userType": user_type,
        "userId": actor_id,
        "userPrincipalName": actor_name,
        "service": service,
        "objectId": object_id,
        "clientIp": None,
        "administrativeUnits": [],
        "auditData": {
            "@odata.type": "#microsoft.graph.security.defaultAuditData",
            "dynamicProperties": data,
        },
    }
    validate_audit_record(payload, tenant=source.ref.tenant_id, occurred_at=source.occurred_at)
    return ProjectionBatch(
        source=source.ref,
        generation=source.generation,
        receipt_status="projected",
        items=(
            Projection(
                family="auditLogRecord",
                id=identifier,
                occurred_at=source.occurred_at,
                available_at=source.available_at,
                payload=payload,
                subject_id=None,
                source_ref=source.ref,
            ),
        ),
    )
