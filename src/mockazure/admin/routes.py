from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from mockazure.admin.errors import AdminError
from mockazure.db.migrations import schema_is_current
from mockazure.identity.keys import SigningKeyStore, identity_ready, read_wrapping_key
from mockazure.tenants.repository import create_tenant, get_tenant, list_tenants
from mockazure.tenants.schemas import TenantCreate, TenantPage, TenantView

router = APIRouter(prefix="/_mock")


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    if not await schema_is_current(request.app.state.database):
        raise AdminError(503, "NotReady", "Database or schema is not ready")
    if not await identity_ready(request.app.state.settings, request.app.state.database):
        raise AdminError(503, "IdentityNotReady", "Identity wrapping material is unavailable")
    return {"status": "ready"}


@router.get("/capabilities")
async def capabilities(request: Request):
    from mockazure.providers.graph.read_profiles import READ_RESOURCES

    settings = request.app.state.settings
    return {
        "site_drive_profile": {
            "version": "v1.0",
            "beta": False,
            "cursor_seconds": 3600,
            "capability_seconds": 300,
            "import_entries": 1000,
            "import_bytes": 8 * 1024 * 1024,
            "page_rows": 1000,
            "page_bytes": 8 * 1024 * 1024,
            "list_item_expansion": "fields only",
            "selected_scope": "site",
            "delegated_access": "explicit user site ACL",
            "public_content_writes": False,
            "native_permission_facts_to_ual": False,
        },
        "graph_read_budgets": {
            "resources": sorted(READ_RESOURCES),
            "collection_limit": settings.graph_collection_request_limit,
            "collection_window_seconds": settings.graph_collection_window_seconds,
            "directory_limit": settings.graph_directory_request_limit,
            "directory_window_seconds": settings.graph_directory_window_seconds,
            "evidence_limit": 600,
            "evidence_window_seconds": 60,
            "batch_envelope_charged": False,
            "fault_kinds": ["next_read_429", "next_page_429"],
        },
        "milestone": 5,
        "delivery": "collection-evidence",
        "authentication_flows": [
            "client_credentials",
            "authorization_code_pkce",
            "device_code",
            "refresh_token",
            "managed_identity",
        ],
        "provider_operations": [
            "graph.organization.read",
            "graph.me.read",
            "graph.users.lifecycle",
            "graph.groups.static_security.lifecycle",
            "graph.directory.relationships",
            "graph.directory.query_subset",
            "graph.directory.snapshot_paging",
            "graph.directory.delta",
            "graph.batch.read_only",
            "graph.collections.shared_read_budgets",
            "graph.auditLogs.signIns",
            "graph.auditLogs.directoryAudits",
            "graph.identityProtection.riskDetections",
            "graph.identityProtection.riskyUsers",
            "graph.identityProtection.riskyUsers.actions",
            "graph.licensing",
            "graph.applications.lifecycle",
            "graph.servicePrincipals.lifecycle",
            "graph.devices.lifecycle",
            "graph.security.owners",
            "graph.security.credentials",
            "graph.oauth2PermissionGrants",
            "graph.servicePrincipals.appRoleAssignments",
            "graph.directoryRoles",
            "graph.roleManagement.directory",
            "graph.authenticationMethods.inspect",
            "graph.reports.userRegistrationDetails",
            "graph.users.signInActivity",
            "graph.conditionalAccess.policies",
            "graph.conditionalAccess.namedLocations",
            "graph.conditionalAccess.enforcement",
            "graph.sites.read",
            "graph.sites.listEvidence",
            "graph.sites.selectedApplicationGrants",
            "graph.drives.read",
            "graph.drives.permissionsAndVersions.read",
            "graph.drives.localContent.read",
            "graph.drives.rootDelta",
            "graph.mail.inboxRules",
            "graph.mail.folders.read",
            "graph.mail.messages.read",
            "graph.mail.messages.mutate",
            "graph.mail.messages.folderDelta",
            "graph.mail.attachments.read",
            "graph.mail.rawContent.read",
            "graph.mail.mailboxSettings.read",
            "graph.security.auditLog.queries",
            "graph.security.alerts.read",
            "graph.security.alerts_v2.read",
            "graph.security.incidents.read",
            "graph.security.secureScores.read",
            "graph.security.secureScoreControlProfiles.read",
            "exchange.organization.read",
            "exchange.collection.configuration",
            "exchange.collection.permissions",
            "exchange.mailbox.configuration",
            "exchange.mailbox.statistics",
            "exchange.inboxRules",
            "exchange.sweepRules",
            "exchange.messageTrace",
            "arm.subscriptions.list",
            "arm.resources.inventory",
            "arm.resourceGroups.lifecycle",
            "arm.tags",
            "arm.roleDefinitions",
            "arm.roleAssignments",
            "arm.activityLogs",
        ],
        "limitations": [
            "Local identities only; no upstream fallback",
            "Sites/drives: imported v1.0 evidence; no public content edits or sharing writes",
            "Site grants retain native facts without an inferred Purview operation mapping",
            "Issued download capabilities stay valid for five minutes after grant revocation",
            "Bounded queries and Conditional Access targets/IP locations/"
            "block/MFA/report-only profile",
            "Physical MFA, passwordless and SSPR services are outside the descriptor profile",
            "No proprietary risk scoring or unsupported device/platform/session policy controls",
            "Exchange command proxy qualified only with module 3.11.0",
            "Mailbox configuration/rules do not execute mail delivery",
            "Graph mail: v1.0 primary mailbox only; explicit content imports; no send",
            "Mail raw bytes remain imported evidence; reference attachments have no raw content",
            "Inbox rules: 1000 per mailbox, 100 per page; no filter or expand",
            "UAL queries: seven-day retained results, one-hour continuation handles",
            "Maintenance cleanup is bounded to1000 rows; query/job tombstones are retained",
            "ARM uses explicit discovery/resource/RBAC/Activity API-version allowlists",
            "ARM roles: Reader/Contributor/Owner/User Access Administrator; no custom-role writes",
            "Inventory pages invalidate on mutation; Activity pages retain publication snapshots",
            "Activity times retain 100ns precision; query grammar and mutations are bounded",
            "No Azure compute deployment or arbitrary resource-provider execution",
            "Security collections: five v1.0 read families, typed imports, one-hour cursors",
            "Legacy alerts reproduce historical collector behavior after upstream retirement",
            "Security evidence does not execute detection, hunting or remediation",
            "Tenant exports remain metadata-only; directory datasets are separate public fixtures",
        ],
        "admin_operations": [
            "workloads.sharepoint.import",
            "workloads.sharepoint.export",
            "workloads.sharepoint.reset",
            "workloads.sharepoint.grants",
            "workloads.sharepoint.facts",
            "mail.collectionState",
            "mail.content.import",
            "mail.content.export",
            "mail.content.reset",
            "content.upload",
            "content.download",
            "content.manifest",
            "content.validate",
            "content.reset",
            "tenant.create",
            "tenant.get",
            "tenant.list",
            "tenant.export",
            "tenant.import",
            "job.get",
            "job.result",
            "identity.users",
            "identity.applications",
            "identity.credentials",
            "identity.grants",
            "identity.directory_roles",
            "security.authenticationMethods",
            "identity.sessions",
            "identity.signing_keys.rotate",
            "mail.settings",
            "mail.mailboxes",
            "mail.command_grants",
            "mail.rule_seed",
            "mail.trace_import",
            "mail.audit_roles",
            "arm.subscriptions",
            "arm.reader_assignments",
            "arm.inventory",
            "arm.role_assignments",
            "arm.activity_faults",
            "directory.import",
            "directory.export",
            "directory.cleanup",
            "graph.read_faults",
            "licensing.inventory",
            "managed_identity.identities",
            "managed_identity.hosts",
            "collections.import",
            "evidence.import",
            "evidence.export",
            "evidence.settings",
            "evidence.clock",
            "evidence.reset",
            "evidence.cleanup",
            "maintenance.cleanup",
            "evidence.jobs",
            "evidence.risk_observations",
        ],
    }


@router.post("/tenants", status_code=201, response_model=TenantView)
async def tenant_create(data: TenantCreate, request: Request, response: Response):
    async with request.app.state.database.sessions.begin() as session:
        tenant = await create_tenant(session, data)
    response.headers["Location"] = (
        str(request.app.state.settings.admin_base_url).rstrip("/") + f"/_mock/tenants/{tenant.id}"
    )
    return tenant


@router.get("/tenants", response_model=TenantPage)
async def tenant_list(
    request: Request, after: UUID | None = None, limit: int = Query(100, ge=1, le=500)
):
    async with request.app.state.database.sessions() as session:
        rows = await list_tenants(session, after=after, limit=limit + 1)
    return TenantPage(
        items=rows[:limit], next_after=rows[limit - 1].id if len(rows) > limit else None
    )


@router.get("/tenants/{tenant_id}", response_model=TenantView)
async def tenant_get(tenant_id: UUID, request: Request):
    async with request.app.state.database.sessions() as session:
        tenant = await get_tenant(session, tenant_id)
    if tenant is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")
    return tenant


@router.post("/identity/keys/rotate")
async def rotate_identity_keys(request: Request):
    settings = request.app.state.settings
    try:
        wrapping = read_wrapping_key(settings.data_dir)
        async with request.app.state.database.sessions.begin() as session:
            key = await SigningKeyStore(session, wrapping, settings.endpoints).rotate(
                datetime.now(UTC)
            )
            return {"kid": key.kid}
    except ValueError:
        raise AdminError(
            503, "IdentityNotReady", "Identity wrapping material is unavailable"
        ) from None
