"""Authorize every hunting input before loading any row, including joined sources."""

from dataclasses import dataclass
from uuid import UUID, uuid5

from mockazure.authorization.evaluator import AuthorizationDenied, OperationRequirement, authorize
from mockazure.authorization.roles import effective_roles
from mockazure.hunting.access_models import HuntingSettings, HuntingVisibility
from mockazure.hunting.contracts import catalog, table_contract
from mockazure.providers.graph.errors import GraphError

ENTITLEMENTS = {"identity", "email", "endpoint", "cloud-apps", "alerts", "sentinel-ueba"}
READERS = {"Global Administrator", "Global Reader", "Security Administrator", "Security Reader"}
REQUIREMENT = OperationRequirement(
    "graph",
    frozenset({"ThreatHunting.Read.All"}),
    frozenset({"ThreatHunting.Read.All"}),
)
UEBA_COLUMNS = {
    "BlastRadius",
    "CompanyName",
    "DeletedDateTime",
    "EmployeeId",
    "OtherMailAddresses",
    "Tags",
    "State",
    "GroupMembership",
    "RiskStatus",
}


def primary_workspace(tenant_id):
    return uuid5(tenant_id, "mockazure:primary-hunting-workspace")


@dataclass(frozen=True)
class HuntingScope:
    tenant_id: UUID
    workspace_id: UUID
    tables: frozenset[str]
    entitlements: frozenset[str]
    device_ids: frozenset[str] | None


def denied(message):
    raise GraphError(403, "Authorization_RequestDenied", message)


async def authorize_inputs(session, actor, table_names, workspace_id=None):
    try:
        await authorize(actor, REQUIREMENT, session)
    except AuthorizationDenied:
        denied("Current Graph identity and ThreatHunting.Read.All are required")
    if not table_names <= catalog().keys():
        raise GraphError(400, "BadRequest", "Unknown local hunting table")
    settings = await session.get(HuntingSettings, actor.tenant_id, populate_existing=True)
    entitlements = frozenset(settings.entitlements if settings else [])
    if not entitlements or any(
        not set(table_contract(name).entitlements) <= entitlements for name in table_names
    ):
        denied("A required local hunting service entitlement is unavailable")
    device_ids = None
    if actor.token_kind == "delegated":
        visibility = await session.get(
            HuntingVisibility,
            (actor.tenant_id, actor.subject_id),
            populate_existing=True,
        )
        roles = await effective_roles(session, actor.tenant_id, actor.subject_id)
        if visibility is None:
            if not set(roles) & READERS:
                denied("An advanced hunting read role is required")
        else:
            if not visibility.view_data or not table_names <= set(visibility.tables):
                denied("Signed-in user cannot view every requested hunting table")
            if visibility.device_ids is not None:
                device_ids = frozenset(visibility.device_ids)
    # A foreign/missing workspace is never queried. The one local primary is the
    # only supported source, matching the documented fallback without widening scope.
    return HuntingScope(
        actor.tenant_id,
        primary_workspace(actor.tenant_id),
        frozenset(table_names),
        entitlements,
        device_ids,
    )


def visible_columns(name, scope):
    columns = dict(table_contract(name).columns)
    if name == "IdentityInfo" and "sentinel-ueba" not in scope.entitlements:
        for field in UEBA_COLUMNS:
            columns.pop(field, None)
    return columns
