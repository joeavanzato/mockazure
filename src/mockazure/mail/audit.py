"""Pure UAL projection of immutable native mail facts."""

import json
from uuid import uuid5

from mockazure.evidence.schemas import Projection, ProjectionBatch
from mockazure.mail.audit_schemas import validate_audit_record
from mockazure.mail.facts import validate_mail_facts

IGNORED = {"WhenChangedUTC", "EffectiveForwardingAddress"}


def changed_properties(before, after):
    return [
        key
        for key in sorted(before.keys() | after.keys())
        if key not in IGNORED and before.get(key) != after.get(key)
    ]


def audit_value(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


async def record_mail_audit(session, actor, operation, target, before, after, now) -> None:
    from mockazure.mail.facts import record_mail_fact

    await record_mail_fact(session, actor, operation, target, before, after, now)


def project_mail(source):
    facts = validate_mail_facts(source.facts)
    changed = changed_properties(facts["before"], facts["after"])
    if not changed:
        return ProjectionBatch(
            source=source.ref, generation=source.generation, items=(), receipt_status="suppressed"
        )
    identifier = uuid5(source.ref.id, "mockazure:mail:auditLogRecord")
    user, app = facts["actor"].get("user"), facts["actor"].get("app")
    if user:
        actor_id, actor_name, user_type, user_number = (
            user["id"],
            user["userPrincipalName"],
            "Regular",
            0,
        )
    else:
        actor_id = app["servicePrincipalId"]
        actor_name, user_type, user_number = None, "ServicePrincipal", 6
    mailbox = facts["mailbox"]
    object_id = mailbox["upn"]
    if facts["operation"] != "Set-Mailbox":
        rule = facts["after"] or facts["before"]
        object_id += "\\" + rule.get("id", rule.get("RuleIdentity", ""))
    data = {
        "Id": str(identifier),
        "CreationTime": source.occurred_at.isoformat(),
        "RecordType": 1,
        "Operation": facts["operation"],
        "OrganizationId": str(source.ref.tenant_id),
        "UserType": user_number,
        "UserKey": actor_id,
        "UserId": actor_name or actor_id,
        "Workload": "Exchange",
        "ResultStatus": "True",
        "ObjectId": object_id,
        "ClientIP": None,
        "ExternalAccess": False,
        "TokenObjectId": actor_id,
        "TokenTenantId": str(source.ref.tenant_id),
        "MailboxOwnerUPN": mailbox["upn"],
        "MailboxOwnerId": mailbox["id"],
        "ModifiedProperties": [
            {
                "Name": key,
                "OldValue": audit_value(facts["before"].get(key)),
                "NewValue": audit_value(facts["after"].get(key)),
            }
            for key in changed
        ],
    }
    if app:
        data["AppId"] = app["appId"]
    payload = {
        "id": str(identifier),
        "createdDateTime": source.occurred_at.isoformat(),
        "auditLogRecordType": "ExchangeAdmin",
        "operation": facts["operation"],
        "organizationId": str(source.ref.tenant_id),
        "userType": user_type,
        "userId": actor_id,
        "userPrincipalName": actor_name,
        "service": "Exchange",
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
