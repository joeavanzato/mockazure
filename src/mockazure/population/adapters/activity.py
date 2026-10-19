"""Management Activity records retain their native common audit schema."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from mockazure.activity.contracts import validate_record
from mockazure.activity.models import ActivityState, Content
from mockazure.activity.publication import store_records
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, OperationDescriptor


def adapters():
    def normalize(payload):
        validate_record(payload, UUID(payload.get("OrganizationId", "")))
        return payload

    async def write(session, ctx, pending):
        await store_records(
            session, ctx.tenant_id, [payload for _, payload in pending], datetime.now(UTC)
        )

    generation = (
        select(ActivityState.generation)
        .where(ActivityState.tenant_id == Content.tenant_id)
        .correlate(Content)
        .scalar_subquery()
    )
    descriptor = FamilyDescriptor(
        key="activity.records",
        service="Management Activity",
        api_path="/api/v1.0/{tenant}/activity/feed/subscriptions/content",
        display_fields=["Id", "CreationTime", "Operation", "UserId", "Workload"],
        operations=[
            OperationDescriptor(
                key="import",
                mode="fixture",
                method="POST",
                path_template="/_mock/tenants/{tenant}/activity/import",
                api_version="v1.0",
                input_schema={
                    "type": "object",
                    "required": [
                        "Id",
                        "OrganizationId",
                        "CreationTime",
                        "Operation",
                        "UserKey",
                        "UserId",
                        "Workload",
                        "RecordType",
                        "UserType",
                    ],
                },
            )
        ],
        contract_refs=["src/mockazure/activity/contracts.py"],
        limitations=[
            "Records are immutable and use the existing feed generation.",
            "Population does not start a consuming application's feed subscription.",
        ],
    )
    adapter = PayloadAdapter(
        descriptor,
        ReadSpec(
            Content,
            Content.record_id,
            Content.records[0],
            Content.revision,
            (Content.generation == generation,),
        ),
        normalize,
        write,
    )
    adapter.identity_field = "Id"
    return [adapter]
