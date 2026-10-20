"""Current permission/role/licence enforcement before any evidence object lookup."""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text

from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity
from mockazure.authorization.roles import effective_roles
from mockazure.licensing.service import entitlements
from mockazure.providers.graph.errors import GraphError

ROLES = {
    "signIn": {
        "Global Reader",
        "Reports Reader",
        "Security Administrator",
        "Security Operator",
        "Security Reader",
    },
    "directoryAudit": {"Reports Reader", "Security Administrator", "Security Reader"},
    "riskyUser": {
        "Global Reader",
        "Security Operator",
        "Security Reader",
        "Security Administrator",
    },
    "riskDetection": {
        "Global Reader",
        "Security Operator",
        "Security Reader",
        "Security Administrator",
    },
}
PERMISSIONS = {
    "signIn": {"AuditLog.Read.All"},
    "directoryAudit": {"AuditLog.Read.All", "Directory.Read.All"},
    "riskyUser": {"IdentityRiskyUser.Read.All"},
    "riskDetection": {"IdentityRiskEvent.Read.All"},
}


@dataclass(frozen=True)
class EvidenceOperation:
    version: str
    resource: str
    method: str = "GET"
    target_id: UUID | None = None


@dataclass(frozen=True)
class EvidenceAccess:
    subject_id: UUID | None
    entitlement_fingerprint: str
    redaction: str
    conditional_access: bool = False


def denied(message="Permission and supported role are required"):
    raise GraphError(403, "Authorization_RequestDenied", message)


async def authorize_evidence(session, context, operation):
    try:
        await require_current_identity(context, session)
    except AuthorizationDenied:
        denied()
    if operation.resource not in ROLES or operation.version not in {"v1.0", "beta"}:
        denied()
    roles = (
        set(await effective_roles(session, context.tenant_id, context.subject_id))
        if context.token_kind == "delegated"
        else set()
    )
    if operation.method == "POST":
        if operation.resource != "riskyUser" or operation.version != "v1.0":
            denied()
        allowed = {"IdentityRiskyUser.ReadWrite.All"}
        supported = {"Global Administrator", "Security Administrator"}
    elif operation.method == "GET":
        allowed = PERMISSIONS[operation.resource]
        supported = ROLES[operation.resource] | {"Global Administrator"}
    else:
        denied()
    tenant_access = bool(context.permissions & allowed) and (
        context.token_kind == "application" or bool(roles & supported)
    )
    subject = None
    if not tenant_access:
        if (
            operation.method == "GET"
            and operation.resource == "signIn"
            and context.token_kind == "delegated"
            and context.permissions
        ):
            subject = context.subject_id
        else:
            denied()
    entitlement = await entitlements(session, context.tenant_id, context.subject_id)
    required = (
        "P2"
        if operation.resource == "riskyUser"
        else ("P1" if operation.resource in {"signIn", "riskDetection"} else "Free")
    )
    if required == "P2" and entitlement.tenant_tier != "P2":
        denied("Microsoft Entra ID P2 availability is required")
    if required == "P1" and entitlement.tenant_tier == "Free":
        denied("Microsoft Entra ID P1 or P2 availability is required")
    # Hash in PostgreSQL rather than transferring every tenant assignment to Python.
    # Include current user existence because orphan historical disclosure is different.
    state_hash = await session.scalar(
        text("""
SELECT md5(
 COALESCE((SELECT string_agg(user_id::text || ':' || sku_id::text || ':' ||
  disabled_plans::text, ',' ORDER BY user_id,sku_id)
  FROM licensing_user_assignments WHERE tenant_id=:tenant),'') || '|' ||
 COALESCE((SELECT string_agg(id::text || ':' || COALESCE(deleted_at::text,''), ',' ORDER BY id)
  FROM identity_users u WHERE tenant_id=:tenant AND EXISTS(
   SELECT 1 FROM evidence_sources s WHERE s.tenant_id=u.tenant_id AND s.kind='historical'
   AND s.facts->>'orphaned_subject'='true' AND s.facts->>'subject_id'=u.id::text)),'') || '|' ||
 COALESCE((SELECT string_agg(sku_id::text || ':' || status || ':' || enabled::text || ':' ||
  service_plans::text, ',' ORDER BY sku_id) FROM licensing_skus WHERE tenant_id=:tenant),''))
"""),
        {"tenant": context.tenant_id},
    )
    fingerprint = hashlib.sha256(
        json.dumps(
            [entitlement.fingerprint, state_hash, sorted(roles), str(subject)],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    ca_details = bool(
        context.permissions
        & {"Policy.Read.All", "Policy.Read.ConditionalAccess", "Policy.ReadWrite.ConditionalAccess"}
    ) and (
        context.token_kind == "application"
        or bool(
            roles
            & {
                "Global Administrator",
                "Global Reader",
                "Security Administrator",
                "Security Reader",
                "Conditional Access Administrator",
            }
        )
    )
    return EvidenceAccess(subject, fingerprint, entitlement.tenant_tier, ca_details)
