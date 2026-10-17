"""Supported Exchange cmdlet projections of canonical Graph mailbox rules."""

ACTION_NAMES = {
    "forwardTo": "ForwardTo",
    "redirectTo": "RedirectTo",
    "forwardAsAttachmentTo": "ForwardAsAttachmentTo",
    "moveToFolder": "MoveToFolder",
    "copyToFolder": "CopyToFolder",
    "delete": "DeleteMessage",
    "markAsRead": "MarkAsRead",
    "stopProcessingRules": "StopProcessingRules",
    "markImportance": "MarkImportance",
}
CONDITION_NAMES = {
    "subjectContains": "SubjectContainsWords",
    "bodyContains": "BodyContainsWords",
    "bodyOrSubjectContains": "SubjectOrBodyContainsWords",
    "headerContains": "HeaderContainsWords",
    "fromAddresses": "From",
    "sentToAddresses": "SentTo",
    "hasAttachments": "HasAttachment",
}
RECIPIENT_FIELDS = {"ForwardTo", "RedirectTo", "ForwardAsAttachmentTo", "From", "SentTo"}


def exchange_rule_view(box, row):
    payload = row.payload
    result = {
        "Identity": box.alias + "\\" + row.id,
        "RuleIdentity": row.id,
        "Name": payload["displayName"],
        "Enabled": payload.get("isEnabled", True),
        "Priority": row.sequence - 1,
        "IsValid": True,
        "SupportedByTask": not row.read_only,
        "IsHidden": row.hidden,
    }
    for source, mapping in (("actions", ACTION_NAMES), ("conditions", CONDITION_NAMES)):
        for field, name in mapping.items():
            value = (payload.get(source) or {}).get(field)
            if name in RECIPIENT_FIELDS:
                value = [
                    {
                        "Address": recipient["emailAddress"]["address"],
                        "Name": recipient["emailAddress"].get("name")
                        or recipient["emailAddress"]["address"],
                    }
                    for recipient in value or []
                ]
            result[name] = value
    result["Description"] = (
        payload["displayName"]
        + ": "
        + "; ".join(
            f"{key}={value}"
            for key, value in result.items()
            if key in set(ACTION_NAMES.values()) | set(CONDITION_NAMES.values()) and value
        )
    )
    return result


def sweep_view(box, row):
    return {"Identity": box.alias + "\\" + row.id, "RuleIdentity": row.id, **row.payload}
