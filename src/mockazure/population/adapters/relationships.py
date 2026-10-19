"""Append validated relationships without replacing an existing source's edges."""

import json

from sqlalchemy import String, cast, func

from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.adapters.workload_limits import bounded_import_workload
from mockazure.population.types import FamilyDescriptor, OperationDescriptor
from mockazure.workloads.imports import WorkloadEdge, WorkloadImport
from mockazure.workloads.models import WorkloadRelation
from mockazure.workloads.repository import scope_row


def adapters():
    entries = []
    for workload in ("sharepoint", "teams", "intune"):

        def normalize(payload):
            edge = WorkloadEdge.model_validate(payload)
            if any(key.storage_key not in {None, key.id} for key in (edge.source, edge.target)):
                raise ValueError("Use the bulk interface for custom storage keys")
            source, relation, target = edge.source, edge.relation, edge.target
            return {
                "id": json.dumps(
                    [source.kind, source.id, relation, target.kind, target.id], ensure_ascii=False
                ),
                "source": {"kind": source.kind, "id": source.id},
                "relation": relation,
                "target": {"kind": target.kind, "id": target.id},
            }

        async def write(session, ctx, pending, workload=workload):
            for mutation, payload in pending:
                if not mutation.parent:
                    raise ValueError("Workload scope is required")
                state = await scope_row(
                    session, ctx.tenant_id, workload, mutation.parent, lock=True
                )
                await bounded_import_workload(
                    session,
                    ctx.tenant_id,
                    workload,
                    WorkloadImport.model_validate(
                        {
                            "scope": mutation.parent,
                            "expected_revision": state.revision,
                            "edges": [{k: v for k, v in payload.items() if k != "id"}],
                        }
                    ),
                )

        r = WorkloadRelation
        identifier = cast(
            func.jsonb_build_array(
                r.source_kind, r.source_id, r.relation, r.target_kind, r.target_id
            ),
            String,
        )
        view = json_object(
            id=identifier,
            source=json_object(kind=r.source_kind, id=r.source_id),
            relation=r.relation,
            target=json_object(kind=r.target_kind, id=r.target_id),
        )
        descriptor = FamilyDescriptor(
            key=workload + ".relationships",
            service=workload,
            api_path="/v1.0/{resource}/{relationship}",
            display_fields=["source", "relation", "target"],
            operations=[
                OperationDescriptor(
                    key="link",
                    mode="fixture",
                    method="POST",
                    api_version="local-fixture-v1",
                    path_template="/_mock/tenants/{tenant}/workloads/" + workload + "/import",
                    input_schema=WorkloadEdge.model_json_schema(),
                )
            ],
            contract_refs=["COVERAGE.md"],
            limitations=[
                "Append-only relationships between existing canonical keys; bulk interface "
                "supports custom storage keys."
            ],
        )
        entries.append(
            PayloadAdapter(
                descriptor,
                ReadSpec(
                    r,
                    identifier,
                    view,
                    r.valid_from,
                    (r.workload == workload, r.valid_to.is_(None)),
                    parent=r.scope,
                ),
                normalize,
                write,
            )
        )
    return entries
