"""Workload editing preserves outgoing edges unless explicitly managed elsewhere."""

from collections import defaultdict
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select, tuple_

from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.adapters.workload_limits import bounded_import_workload
from mockazure.population.corpus.models import ContentSpec
from mockazure.population.corpus.render import render
from mockazure.population.corpus.storage import store_rendered
from mockazure.population.types import FamilyDescriptor, Input, OperationDescriptor
from mockazure.workloads.imports import WorkloadEdge, WorkloadImport
from mockazure.workloads.intune import validate_intune_record
from mockazure.workloads.intune_contracts import KINDS as INTUNE_KINDS
from mockazure.workloads.models import WorkloadRelation, WorkloadVersion
from mockazure.workloads.repository import scope_row
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


class LinkedRecord(Input):
    payload: dict
    edges: list[WorkloadEdge] = Field(default_factory=list, max_length=100)
    content_id: UUID | None = None
    document: ContentSpec | None = None

    @model_validator(mode="after")
    def one_content_source(self):
        if self.document is not None and self.content_id is not None:
            raise ValueError("Choose a generated document or existing content")
        return self


class WorkloadAdapter(PayloadAdapter):
    async def matches_mutation(self, session, ctx, old, mutation, payload):
        if not self.matches(old.properties, payload):
            return False
        if mutation.operation != "import-linked":
            return True
        linked = LinkedRecord.model_validate(mutation.payload)
        workload, kind = self.descriptor.key.split(".", 1)
        if linked.document is not None:
            from mockazure.content.models import ContentObject

            rendered = render(linked.document)
            content = await session.scalar(
                select(ContentObject)
                .join(
                    WorkloadVersion,
                    (WorkloadVersion.tenant_id == ContentObject.tenant_id)
                    & (WorkloadVersion.content_id == ContentObject.id),
                )
                .where(
                    WorkloadVersion.tenant_id == ctx.tenant_id,
                    WorkloadVersion.workload == workload,
                    WorkloadVersion.scope == mutation.parent,
                    WorkloadVersion.kind == kind,
                    WorkloadVersion.id == old.id,
                    WorkloadVersion.valid_to.is_(None),
                )
            )
            if content is None or (
                content.sha256,
                content.size,
                content.media_type,
                content.state,
            ) != (rendered.sha256, rendered.length, rendered.media_type, "finalized"):
                return False
        if "content_id" in linked.model_fields_set:
            current = await session.scalar(
                select(WorkloadVersion.content_id).where(
                    WorkloadVersion.tenant_id == ctx.tenant_id,
                    WorkloadVersion.workload == workload,
                    WorkloadVersion.scope == mutation.parent,
                    WorkloadVersion.kind == kind,
                    WorkloadVersion.id == old.id,
                    WorkloadVersion.valid_to.is_(None),
                )
            )
            if current != linked.content_id:
                return False
        edge = WorkloadRelation
        for desired in linked.edges:
            source_kind, source_id, relation, target_kind, target_id = desired.key()
            found = await session.scalar(
                select(edge.source_id)
                .where(
                    edge.tenant_id == ctx.tenant_id,
                    edge.workload == workload,
                    edge.scope == mutation.parent,
                    edge.source_kind == source_kind,
                    edge.source_id == source_id,
                    edge.relation == relation,
                    edge.target_kind == target_kind,
                    edge.target_id == target_id,
                    edge.valid_to.is_(None),
                )
                .limit(1)
            )
            if found is None:
                return False
        return True

    def normalize(self, mutation):
        if mutation.operation == "import-linked":
            value = LinkedRecord.model_validate(mutation.payload)
            if value.document is not None:
                if self.descriptor.key not in {
                    "sharepoint.driveItem",
                    "sharepoint.driveItemVersion",
                }:
                    raise ValueError("Generated document requires a file or file version")
                rendered = render(value.document)
                if value.payload.get("size") != rendered.length:
                    raise ValueError("Document metadata size differs from generated bytes")
            return self.normalizer(value.payload)
        return super().normalize(mutation)


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
                    grouped[mutation.parent].append((mutation, payload))
                for scope, entries in grouped.items():
                    records = [payload for _, payload in entries]
                    state = await scope_row(
                        session, ctx.tenant_id, workload, scope, create=True, lock=True
                    )
                    edges = await preserved_edges(
                        session, ctx.tenant_id, workload, scope, kind, [r["id"] for r in records]
                    )
                    current = WorkloadVersion
                    preserved = dict(
                        (
                            await session.execute(
                                select(current.id, current.content_id).where(
                                    current.tenant_id == ctx.tenant_id,
                                    current.workload == workload,
                                    current.scope == scope,
                                    current.kind == kind,
                                    current.valid_to.is_(None),
                                    current.id.in_([r["id"] for r in records]),
                                )
                            )
                        ).all()
                    )
                    bound = []
                    for mutation, payload in entries:
                        content = preserved.get(payload["id"])
                        if mutation.operation == "import-linked":
                            linked = LinkedRecord.model_validate(mutation.payload)
                            if "content_id" in linked.model_fields_set:
                                content = linked.content_id
                            if linked.document is not None:
                                saved = await store_rendered(
                                    session,
                                    ctx.tenant_id,
                                    render(linked.document),
                                    ctx.settings.content_max_object_bytes,
                                )
                                content = saved.id
                            edges.extend(
                                e.model_dump(mode="json", by_alias=True) for e in linked.edges
                            )
                        bound.append(
                            {
                                "kind": kind,
                                "id": payload["id"],
                                "payload": payload,
                                "content_id": content,
                            }
                        )
                    edges = list(
                        {str(WorkloadEdge.model_validate(e).key()): e for e in edges}.values()
                    )
                    batch = WorkloadImport.model_validate(
                        {
                            "scope": scope,
                            "expected_revision": state.revision,
                            "records": bound,
                            "edges": edges,
                        }
                    )
                    await bounded_import_workload(session, ctx.tenant_id, workload, batch)

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
                    ),
                    OperationDescriptor(
                        key="import-linked",
                        mode="fixture",
                        method="POST",
                        path_template=f"/_mock/tenants/{{tenant}}/workloads/{workload}/import",
                        api_version="v1.0",
                        input_schema=LinkedRecord.model_json_schema(),
                    ),
                ],
                contract_refs=["src/mockazure/providers/graph/schemas/README.md"],
                limitations=[
                    "Scope is required; existing outgoing relationships are preserved.",
                    "Use import-linked for atomic relationships/content; bulk import for "
                    "custom keys.",
                ],
            )
            row = WorkloadVersion
            result.append(
                WorkloadAdapter(
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
