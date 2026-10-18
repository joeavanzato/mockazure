"""OAuth authority and separate application resource grants / user site ACLs."""

from sqlalchemy import and_, exists, select

from mockazure.admin.errors import AdminError
from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.identity.models import ServicePrincipal, User
from mockazure.providers.graph.errors import GraphError
from mockazure.workloads.models import WorkloadGrant as G
from mockazure.workloads.repository import current_record, scope_row

SITE_READ = frozenset(
    {"Sites.Read.All", "Sites.ReadWrite.All", "Sites.Manage.All", "Sites.FullControl.All"}
)
DRIVE_READ = SITE_READ | {"Files.Read.All", "Files.ReadWrite.All"}


def requirement(operation):
    perms = (
        frozenset({"Sites.FullControl.All"})
        if operation == "permissions"
        else (DRIVE_READ if operation == "drive" else SITE_READ)
    )
    delegated = perms | ({"Files.Read", "Files.ReadWrite"} if operation == "drive" else set())
    if operation not in {"permissions", "search"}:
        perms, delegated = perms | {"Sites.Selected"}, delegated | {"Sites.Selected"}
    return OperationRequirement(
        "graph",
        perms,
        delegated,
        frozenset({"Global Administrator", "SharePoint Administrator"})
        if operation == "permissions"
        else frozenset(),
    )


async def authorize_operation(session, context, operation):
    try:
        await authorize(context, requirement(operation), session)
    except AuthorizationDenied:
        raise GraphError(
            403, "accessDenied", "Operation requires current identity and Graph permission"
        ) from None


def grant_exists(context, scope, kind, identifier):
    return exists(
        select(1).where(
            G.tenant_id == context.tenant_id,
            G.workload == "sharepoint",
            G.scope == scope,
            G.principal_kind == kind,
            G.principal_id == identifier,
        )
    )


def access_predicate(context, scope, operation):
    conditions = []
    all_permissions = DRIVE_READ if operation == "drive" else SITE_READ
    if not context.permissions & all_permissions:
        if "Sites.Selected" in context.permissions:
            conditions.append(grant_exists(context, scope, "application", context.client_id))
        elif context.token_kind != "delegated" or not context.permissions & {
            "Files.Read",
            "Files.ReadWrite",
        }:
            return False
    if context.token_kind == "delegated":
        conditions.append(grant_exists(context, scope, "user", context.subject_id))
    return and_(*conditions) if conditions else True


async def authorize_site(session, context, site_id: str, operation: str):
    await authorize_operation(session, context, operation)
    if not await session.scalar(select(access_predicate(context, site_id, operation))):
        raise GraphError(
            403, "accessDenied", "The resource grant or signed-in user's site access is missing"
        )
    site = await current_record(session, context.tenant_id, "sharepoint", "site", site_id)
    if site is None:
        raise GraphError(404, "itemNotFound", "Site was not found")


async def set_grant(session, tenant, scope, principal_kind, principal_id, role, display_name=None):
    state = await scope_row(session, tenant, "sharepoint", scope, lock=True)
    if state is None or await current_record(session, tenant, "sharepoint", "site", scope) is None:
        raise AdminError(404, "SiteNotFound", "Site was not found")
    model = User if principal_kind == "user" else ServicePrincipal
    identity = model.id if principal_kind == "user" else model.client_id
    if not await session.scalar(
        select(model.id).where(
            model.tenant_id == tenant,
            identity == principal_id,
            model.enabled.is_(True),
            model.deleted_at.is_(None),
        )
    ):
        raise AdminError(422, "InvalidGrantPrincipal", "Active local principal is required")
    row = await session.scalar(
        select(G).where(
            G.tenant_id == tenant,
            G.workload == "sharepoint",
            G.scope == scope,
            G.principal_kind == principal_kind,
            G.principal_id == principal_id,
        )
    )
    if row is None:
        row = G(
            tenant_id=tenant,
            workload="sharepoint",
            scope=scope,
            principal_kind=principal_kind,
            principal_id=principal_id,
            role=role,
        )
        session.add(row)
    row.role, row.display_name = role, display_name
    state.grant_revision += 1
    await session.flush()
    return row


async def remove_grant(session, tenant, identifier, *, scope=None):
    row = await session.get(G, identifier)
    if (
        row is None
        or row.tenant_id != tenant
        or row.workload != "sharepoint"
        or (scope is not None and row.scope != scope)
    ):
        raise AdminError(404, "GrantNotFound", "Grant was not found")
    state = await scope_row(session, tenant, "sharepoint", row.scope, lock=True)
    state.grant_revision += 1
    await session.delete(row)
