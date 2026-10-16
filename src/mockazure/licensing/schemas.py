from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from mockazure.identity.types import IdentityInput
from mockazure.licensing.catalog import COUNTRIES


class ServicePlan(IdentityInput):
    servicePlanId: UUID
    servicePlanName: str = Field(min_length=1, max_length=200)
    provisioningStatus: Literal[
        "Success", "Disabled", "Error", "PendingInput", "PendingActivation", "PendingProvisioning"
    ]
    appliesTo: Literal["User"] = "User"


class SkuDefinition(IdentityInput):
    sku_part_number: str = Field(min_length=1, max_length=200)
    status: Literal["Enabled", "Warning", "Suspended", "Deleted", "LockedOut"] = "Enabled"
    enabled: int = Field(ge=0, le=2147483647, strict=True)
    suspended: int = Field(default=0, ge=0, le=2147483647, strict=True)
    warning: int = Field(default=0, ge=0, le=2147483647, strict=True)
    service_plans: list[ServicePlan] = Field(min_length=1, max_length=100)
    allowed_usage_locations: list[str] = Field(default_factory=list, max_length=249)

    @field_validator("allowed_usage_locations")
    @classmethod
    def locations(cls, value):
        if len(set(value)) != len(value) or not set(value) <= COUNTRIES:
            raise ValueError("Unsupported country code")
        return sorted(value)

    @field_validator("service_plans")
    @classmethod
    def unique_plans(cls, value):
        if len({p.servicePlanId for p in value}) != len(value):
            raise ValueError("Duplicate service plan")
        return sorted(value, key=lambda p: p.servicePlanId)

    @model_validator(mode="after")
    def graph_schema(self):
        from mockazure.providers.graph.schema_reference import validate_payload

        validate_payload(
            "v1.0",
            "subscribedSku",
            {
                "skuPartNumber": self.sku_part_number,
                "servicePlans": [plan.model_dump(mode="json") for plan in self.service_plans],
            },
            partial=True,
        )
        return self


class SkuInput(SkuDefinition):
    sku_id: UUID


class SkuView(SkuInput):
    id: UUID
    consumed_units: int


class AssignedLicense(IdentityInput):
    skuId: UUID
    disabledPlans: list[UUID] = Field(default_factory=list, max_length=100)

    @field_validator("disabledPlans")
    @classmethod
    def unique_plans(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("Duplicate disabled plan")
        return sorted(value)


class LicenceAssignment(IdentityInput):
    addLicenses: list[AssignedLicense] = Field(default_factory=list, max_length=20)
    removeLicenses: list[UUID] = Field(max_length=20)

    @model_validator(mode="after")
    def distinct(self):
        adds = [v.skuId for v in self.addLicenses]
        removes = self.removeLicenses
        if (
            len(set(adds)) != len(adds)
            or len(set(removes)) != len(removes)
            or set(adds) & set(removes)
        ):
            raise ValueError("Duplicate or conflicting SKU")
        if len(adds) + len(removes) > 20:
            raise ValueError("At most20 SKUs per operation")
        return self


@dataclass(frozen=True)
class Entitlements:
    tenant_tier: Literal["Free", "P1", "P2"]
    user_plan_ids: frozenset[UUID]
    fingerprint: str
