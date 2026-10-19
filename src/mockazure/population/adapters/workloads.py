"""Workload editing preserves outgoing edges unless explicitly managed elsewhere."""

from collections import defaultdict

from sqlalchemy import select, tuple_

from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, OperationDescriptor
from mockazure.workloads.imports import WorkloadImport
from mockazure.workloads.intune import validate_intune_record
from mockazure.workloads.intune_contracts import KINDS as INTUNE_KINDS
from mockazure.workloads.models import WorkloadRelation, WorkloadVersion
from mockazure.workloads.repository import import_workload, scope_row
from mockazure.workloads.sites import KINDS as SITE_KINDS
from mockazure.workloads.sites import validate_site_drive_record
from mockazure.workloads.teams import KINDS as TEAM_KINDS
from mockazure.workloads.teams import validate_team_record

PATHS = {
    "site": "/v1.0/sites/{site-id}",
    "drive": "/v1.0/drives/{drive-id}",
    "driveItem": "/v1.0/drives/{drive-id}/items/{item-id}",
    "list": "/v1.0/sites/{site-id}/lists/{list-id}",
    "listItem": "/v1.0/sites/{site-id}/lists/{list-id}/items/{item-id}",
    "fieldValueSet": "/v1.0/sites/{site-id}/lists/{list-id}/items/{item-id}/fields",
    "permission": "/v1.0/drives/{drive-id}/items/{item-id}/permissions/{permission-id}",
    "driveItemVersion": "/v1.0/drives/{drive-id}/items/{item-id}/versions/{version-id}",
    "team": "/v1.0/teams/{team-id}",
    "channel": "/v1.0/teams/{team-id}/channels/{channel-id}",
    "chat": "/v1.0/chats/{chat-id}",
    "chatMessage": "/v1.0/chats/{chat-id}/messages/{message-id}",
    "conversationMember": "/v1.0/chats/{chat-id}/members/{member-id}",
    "chatMessageHostedContent": (
        "/v1.0/chats/{chat-id}/messages/{message-id}/hostedContents/{content-id}"
    ),
    "filesFolderReference": "/v1.0/teams/{team-id}/channels/{channel-id}/filesFolder",
    "managedDevice": "/v1.0/deviceManagement/managedDevices/{device-id}",
    "detectedApp": "/v1.0/deviceManagement/detectedApps/{app-id}",
    "deviceConfiguration": "/v1.0/deviceManagement/deviceConfigurations/{configuration-id}",
    "deviceCompliancePolicy": "/v1.0/deviceManagement/deviceCompliancePolicies/{policy-id}",
    "deviceConfigurationAssignment": (
        "/v1.0/deviceManagement/deviceConfigurations/{configuration-id}/assignments"
    ),
    "deviceCompliancePolicyAssignment": (
        "/v1.0/deviceManagement/deviceCompliancePolicies/{policy-id}/assignments"
    ),
    "deviceConfigurationDeviceStatus": (
        "/v1.0/deviceManagement/deviceConfigurations/{configuration-id}/deviceStatuses"
    ),
    "deviceComplianceDeviceStatus": (
        "/v1.0/deviceManagement/deviceCompliancePolicies/{policy-id}/deviceStatuses"
    ),
}


async def preserved_edges(session, tenant, workload, scope, kind, identifiers):
    edge = WorkloadRelation
    rows = list(
        await session.scalars(
            select(edge)
            .where(
                edge.tenant_id == tenant,
                edge.workload == workload,
                edge.scope == scope,
                edge.source_kind == kind,
                edge.source_id.in_(identifiers),
                edge.valid_to.is_(None),
            )
            .limit(1001)
        )
    )
    if len(rows) > 1000:
        raise ValueError("Use the bulk workload interface for this relationship set")
    keys = {(r.source_kind, r.source_id) for r in rows} | {
        (r.target_kind, r.target_id) for r in rows
    }
    version = WorkloadVersion
    public_ids = {}
    if keys:
        public_ids = {
            (r.kind, r.id): r.public_id
            for r in await session.execute(
                select(
                    version.kind, version.id, version.payload["id"].as_string().label("public_id")
                ).where(
                    version.tenant_id == tenant,
                    version.workload == workload,
                    version.scope == scope,
                    version.valid_to.is_(None),
                    tuple_(version.kind, version.id).in_(keys),
                )
            )
        }

    def endpoint(kind, key):
        return {"kind": kind, "id": public_ids.get((kind, key), key), "key": key}

    return [
        {
            "source": endpoint(row.source_kind, row.source_id),
            "relation": row.relation,
            "target": endpoint(row.target_kind, row.target_id),
        }
        for row in rows
    ]


def adapters():
    result = []
    for workload, kinds, validator in (
        ("sharepoint", SITE_KINDS, validate_site_drive_record),
        ("teams", TEAM_KINDS, validate_team_record),
        ("intune", INTUNE_KINDS, validate_intune_record),
    ):
        for kind in sorted(kinds):

            def normalize(payload, kind=kind, validator=validator):
                validator(kind, payload)
                return payload

            async def write(session, ctx, pending, workload=workload, kind=kind):
                grouped = defaultdict(list)
                for mutation, payload in pending:
                    if not mutation.parent:
                        raise ValueError("Workload scope is required")
                    grouped[mutation.parent].append(payload)
                for scope, records in grouped.items():
                    state = await scope_row(
                        session, ctx.tenant_id, workload, scope, create=True, lock=True
                    )
                    edges = await preserved_edges(
                        session, ctx.tenant_id, workload, scope, kind, [r["id"] for r in records]
                    )
                    batch = WorkloadImport.model_validate(
                        {
                            "scope": scope,
                            "expected_revision": state.revision,
                            "records": [
                                {"kind": kind, "id": row["id"], "payload": row} for row in records
                            ],
                            "edges": edges,
                        }
                    )
                    await import_workload(session, ctx.tenant_id, workload, batch)

            descriptor = FamilyDescriptor(
                key=f"{workload}.{kind}",
                service=workload,
                api_path=PATHS[kind],
                display_fields=["id", "displayName", "name"],
                operations=[
                    OperationDescriptor(
                        key="import",
                        mode="fixture",
                        method="POST",
                        path_template=f"/_mock/tenants/{{tenant}}/workloads/{workload}/import",
                        api_version="v1.0",
                        input_schema={
                            "type": "object",
                            "required": ["id"],
                            "x-microsoft-type": kind,
                        },
                    )
                ],
                contract_refs=["src/mockazure/providers/graph/schemas/README.md"],
                limitations=[
                    "Scope is required; existing outgoing relationships are preserved.",
                    "Use bulk import for custom keys, new relationships or content bindings.",
                ],
            )
            row = WorkloadVersion
            result.append(
                PayloadAdapter(
                    descriptor,
                    ReadSpec(
                        row,
                        row.id,
                        row.payload,
                        row.valid_from,
                        (
                            row.workload == workload,
                            row.kind == kind,
                            row.valid_to.is_(None),
                            row.deleted.is_(False),
                        ),
                        row.scope,
                    ),
                    normalize,
                    write,
                    replace_allowed=True,
                )
            )
    return result
