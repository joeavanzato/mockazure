"""Historical evidence uses the existing asynchronous projection pipeline."""

from sqlalchemy import select

from mockazure.admin.errors import AdminError
from mockazure.evidence.datasets import EvidenceDataset, EvidenceRecord, import_evidence
from mockazure.evidence.models import EvidenceState, EvidenceVersion
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import (
    BatchResult,
    FamilyDescriptor,
    OperationDescriptor,
    ResourceItem,
    ResourceQuery,
    VerificationResult,
)

PATHS = {
    "signIn": "/v1.0/auditLogs/signIns",
    "directoryAudit": "/v1.0/auditLogs/directoryAudits",
    "riskyUser": "/v1.0/identityProtection/riskyUsers",
    "riskDetection": "/v1.0/identityProtection/riskDetections",
    "auditLogRecord": "/v1.0/security/auditLog/queries/{query-id}/records",
    "activityLog": (
        "/subscriptions/{subscription-id}/providers/microsoft.insights/eventtypes/management/values"
    ),
}


class EvidenceAdapter(PayloadAdapter):
    def __init__(self, family):
        self.family = family
        row = EvidenceVersion
        generation = (
            select(EvidenceState.generation)
            .where(EvidenceState.tenant_id == row.tenant_id)
            .correlate(row)
            .scalar_subquery()
        )
        descriptor = FamilyDescriptor(
            key=f"evidence.{family}",
            service="Evidence",
            api_path=PATHS[family],
            display_fields=["id", "createdDateTime", "userPrincipalName", "activityDisplayName"],
            operations=[
                OperationDescriptor(
                    key="import",
                    mode="fixture",
                    method="POST",
                    path_template="/_mock/tenants/{tenant}/evidence/import",
                    api_version="1",
                    input_schema={
                        "type": "object",
                        "required": ["id", "payload", "occurred_at", "available_at"],
                    },
                )
            ],
            contract_refs=["docs/evidence.md"],
            limitations=[
                "Historical import is immutable; publication is asynchronous.",
                "Shows current-generation publication; public permissions still apply.",
            ],
        )
        super().__init__(
            descriptor,
            ReadSpec(
                row,
                row.record_id,
                row.payload,
                row.valid_from,
                (row.family == family, row.valid_to.is_(None), row.generation == generation),
            ),
            self.normalize_record,
            None,
        )

    def record(self, payload):
        if "id" not in payload or "payload" not in payload:
            raise ValueError("Evidence requires id and historical record envelope")
        record = EvidenceRecord.model_validate(
            {
                "family": self.family,
                "api_version": "2015-04-01" if self.family == "activityLog" else "v1.0",
                **{key: value for key, value in payload.items() if key != "id"},
            }
        )
        if record.payload.get("id", record.payload.get("eventDataId")) != payload["id"]:
            raise ValueError("Evidence identity does not match payload")
        return record

    def normalize_record(self, payload):
        record = self.record(payload)
        return {**record.payload, "id": payload["id"]}

    async def apply(self, ctx, mutations):
        if not 1 <= len(mutations) <= 1000:
            raise ValueError("Expected 1 to 1000 records")
        records = []
        for mutation in mutations:
            self.validate(mutation)
            inspection = await self.inspect(ctx, mutation)
            if mutation.expected_version and mutation.expected_version != (
                inspection.version or "absent"
            ):
                raise AdminError(409, "ResourceConflict", "Evidence changed; preview again")
            records.append(self.record(mutation.payload))
        dataset = EvidenceDataset.model_validate(
            {
                "schema_version": 1,
                "provenance": {"source": "MockAzure profiles", "generator": "lab-profiles-v1"},
                "records": [record.model_dump(mode="json") for record in records],
            }
        )
        async with ctx.database.sessions.begin() as session:
            outcome = await import_evidence(session, ctx.tenant_id, dataset)
        items = [
            ResourceItem(id=m.payload["id"], properties=r.payload, version="pending")
            for m, r in zip(mutations, records)
        ]
        return BatchResult(
            items=items,
            effects={"created": outcome.inserted, "updated": 0, "unchanged": outcome.unchanged},
        )

    async def verify(self, ctx, result):
        actual = 0
        for item in result.items:
            page = await self.browse(ctx, ResourceQuery(identifier=item.id))
            if page.items and all(
                page.items[0].properties.get(k) == v for k, v in item.properties.items()
            ):
                actual += 1
        return VerificationResult(
            status="succeeded" if actual == len(result.items) else "pending",
            expected=len(result.items),
            actual=actual,
        )


def adapters():
    return [EvidenceAdapter(family) for family in PATHS]
