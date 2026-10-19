"""Pinned Graph v1.0 security evidence import and conditional replacement."""

from uuid import UUID

from mockazure.collections.contracts import TYPES, validate_records
from mockazure.collections.models import CollectionRecord
from mockazure.collections.repository import import_records
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, OperationDescriptor


def adapters():
    result = []
    for family in TYPES:

        def normalize(payload, family=family):
            tenant = payload.get("tenantId", payload.get("azureTenantId", str(UUID(int=0))))
            return validate_records(family, [payload], UUID(tenant))[0][0]

        async def write(session, ctx, pending, family=family):
            await import_records(
                session, ctx.tenant_id, family, [payload for _, payload in pending]
            )

        fields = ["id", "title", "severity", "status"] if "alert" in family.lower() else ["id"]
        descriptor = FamilyDescriptor(
            key=f"security.{family}",
            service="Graph security",
            api_path=f"/v1.0/security/{family}",
            display_fields=fields,
            operations=[
                OperationDescriptor(
                    key="import",
                    mode="fixture",
                    method="POST",
                    api_version="v1.0",
                    path_template=f"/_mock/tenants/{{tenant}}/collections/{family}/import",
                    input_schema={
                        "type": "object",
                        "required": ["id"],
                        "x-microsoft-type": TYPES[family],
                    },
                )
            ],
            contract_refs=["docs/collection-evidence.md"],
            limitations=["Replacement requires current version and replaces one complete record."],
        )
        result.append(
            PayloadAdapter(
                descriptor,
                ReadSpec(
                    CollectionRecord,
                    CollectionRecord.record_id,
                    CollectionRecord.payload,
                    CollectionRecord.valid_from,
                    (CollectionRecord.family == family, CollectionRecord.valid_to.is_(None)),
                ),
                normalize,
                write,
                replace_allowed=True,
            )
        )
    return result
