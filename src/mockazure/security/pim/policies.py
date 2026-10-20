from copy import deepcopy
from datetime import timedelta
from uuid import uuid5

from mockazure.authorization.evaluator import OperationRequirement
from mockazure.providers.graph.probes import permitted
from mockazure.security.audit import record_security_audit
from mockazure.security.pim.contracts import duration, invalid, typed
from mockazure.security.pim.models import PimPolicy
from mockazure.security.roles import role_ids

ADMINS = frozenset({"Global Administrator", "Privileged Role Administrator"})
READERS = ADMINS | {
    "Global Reader",
    "Security Operator",
    "Security Reader",
    "Security Administrator",
}


def policy_id(tenant, role):
    return str(uuid5(tenant, "pim-policy:" + role_ids()[role]))


def assignment_id(tenant, role):
    return str(uuid5(tenant, "pim-policy-assignment:" + role_ids()[role]))


def defaults():
    result = {}
    for suffix, caller, level, maximum in (
        ("EndUser_Assignment", "EndUser", "Assignment", "PT8H"),
        ("Admin_Assignment", "Admin", "Assignment", "P180D"),
        ("Admin_Eligibility", "Admin", "Eligibility", "P365D"),
    ):
        target = {
            "caller": caller,
            "operations": ["all"],
            "level": level,
            "inheritableSettings": [],
            "enforcedSettings": [],
        }
        key = "Expiration_" + suffix
        result[key] = {
            "@odata.type": "#microsoft.graph.unifiedRoleManagementPolicyExpirationRule",
            "id": key,
            "target": target,
            "isExpirationRequired": True,
            "maximumDuration": maximum,
        }
        key = "Enablement_" + suffix
        result[key] = {
            "@odata.type": "#microsoft.graph.unifiedRoleManagementPolicyEnablementRule",
            "id": key,
            "target": target,
            "enabledRules": ["MultiFactorAuthentication", "Justification"]
            if caller == "EndUser"
            else ["Justification"],
        }
    result["Approval_EndUser_Assignment"] = {
        "@odata.type": "#microsoft.graph.unifiedRoleManagementPolicyApprovalRule",
        "id": "Approval_EndUser_Assignment",
        "target": result["Enablement_EndUser_Assignment"]["target"],
        "setting": {
            "isApprovalRequired": False,
            "isApprovalRequiredForExtension": False,
            "isRequestorJustificationRequired": True,
            "approvalMode": "SingleStage",
            "approvalStages": [],
        },
    }
    return result


async def policy_rules(session, tenant, role):
    row = await session.get(PimPolicy, (tenant, role), populate_existing=True)
    return defaults() | (deepcopy(row.rules) if row else {})


async def require_policy_access(session, actor, write=False):
    names = {"RoleManagementPolicy.ReadWrite.Directory", "RoleManagement.ReadWrite.Directory"}
    if not write:
        names |= {
            "RoleManagementPolicy.Read.Directory",
            "RoleManagement.Read.Directory",
            "RoleManagement.Read.All",
        }
    await permitted(
        actor,
        OperationRequirement(
            "graph", frozenset(names), frozenset(names), ADMINS if write else READERS
        ),
        session,
    )


async def update_rule(session, actor, role, rule_id, body, now):
    await require_policy_access(session, actor, True)
    rules = await policy_rules(session, actor.tenant_id, role)
    if rule_id not in rules or not isinstance(body, dict):
        invalid("Rule is outside the supported policy profile")
    before = rules[rule_id]
    allowed = {"@odata.type", "id", "target"}
    allowed |= (
        {"maximumDuration", "isExpirationRequired"}
        if rule_id.startswith("Expiration_")
        else {"enabledRules"}
        if rule_id.startswith("Enablement_")
        else {"setting"}
    )
    if set(body) - allowed:
        invalid()
    for name in ("@odata.type", "id", "target"):
        if name in body and body[name] != before[name]:
            invalid("Rule identity and target cannot change")
    updated = before | body
    typed(
        before["@odata.type"].split(".")[-1],
        {key: value for key, value in updated.items() if not key.startswith("@")},
    )
    if rule_id.startswith("Expiration_"):
        if type(updated["isExpirationRequired"]) is not bool:
            invalid("Expiration requirement must be a boolean")
        maximum = duration(updated["maximumDuration"])
        if rule_id == "Expiration_EndUser_Assignment" and (
            not updated["isExpirationRequired"] or maximum > timedelta(hours=24)
        ):
            invalid("Activation must expire within 24 hours")
    elif rule_id.startswith("Enablement_"):
        if not isinstance(updated["enabledRules"], list):
            invalid("Enabled rules must be an explicit list")
        if len(updated["enabledRules"]) != len(set(updated["enabledRules"])) or set(
            updated["enabledRules"]
        ) - {"MultiFactorAuthentication", "Justification", "Ticketing"}:
            invalid("Unsupported enablement rule")
    else:
        setting = updated["setting"]
        if not isinstance(setting, dict):
            invalid("Approval settings must be an explicit object")
        if (
            set(setting) != set(before["setting"])
            or setting["approvalStages"]
            or setting["approvalMode"] != "SingleStage"
            or setting["isApprovalRequiredForExtension"]
        ):
            invalid("Approval stages and extension workflows are unsupported")
        if any(
            type(setting[key]) is not bool
            for key in (
                "isApprovalRequired",
                "isApprovalRequiredForExtension",
                "isRequestorJustificationRequired",
            )
        ):
            invalid("Approval requirements must be booleans")
    row = await session.get(PimPolicy, (actor.tenant_id, role))
    if row is None:
        row = PimPolicy(tenant_id=actor.tenant_id, role=role, rules={})
        session.add(row)
    row.rules = dict(row.rules) | {rule_id: updated}
    await session.flush()
    await record_security_audit(
        session,
        actor,
        "Update role management policy",
        {
            "id": policy_id(actor.tenant_id, role),
            "type": updated["@odata.type"].split(".")[-1],
            "displayName": role,
        },
        before,
        updated,
        now,
    )
    return updated
