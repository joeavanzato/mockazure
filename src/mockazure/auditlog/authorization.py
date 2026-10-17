"""Workload permissions and current Purview entitlement/role checks."""

from sqlalchemy import select

from mockazure.auditlog.models import AuditRole
from mockazure.authorization.evaluator import AuthorizationDenied, authorize
from mockazure.mail.contracts import operation_requirement
from mockazure.mail.models import MailSettings

# Explicit workload names from the Common schema; unknown services require all-service permission.
WORKLOADS = {
    "Exchange": ("Exchange",),
    "Entra": ("AzureActiveDirectory",),
    "OneDrive": ("OneDrive",),
    "SharePoint": ("SharePoint",),
    "CRM": ("CRM",),
    "Endpoint": ("Endpoint",),
}


async def authorize_query(session, actor, operation):
    if actor.audience != "00000003-0000-0000-c000-000000000000":
        raise AuthorizationDenied("Graph audience required")
    await authorize(actor, operation_requirement("audit." + operation), session)
    settings = await session.get(MailSettings, actor.tenant_id)
    if settings is None or settings.audit_tier not in {"standard", "premium"}:
        raise AuthorizationDenied("Audit entitlement required")
    if actor.token_kind == "delegated":
        grant = await session.scalar(
            select(AuditRole.user_id)
            .where(
                AuditRole.tenant_id == actor.tenant_id,
                AuditRole.user_id == actor.subject_id,
                AuditRole.role.in_(["Audit Logs", "View-Only Audit Logs"]),
            )
            .limit(1)
        )
        if grant is None:
            raise AuthorizationDenied("Purview audit role required")
    if "AuditLogsQuery.Read.All" in actor.permissions:
        return None
    return frozenset(
        service
        for name, services in WORKLOADS.items()
        if "AuditLogsQuery-" + name + ".Read.All" in actor.permissions
        for service in services
    )


def require_services(allowed, services):
    if allowed is not None and (not services or not set(services) <= allowed):
        raise AuthorizationDenied("Audit workload permission required")
