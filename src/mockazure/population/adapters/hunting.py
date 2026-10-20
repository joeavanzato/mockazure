"""Fixture-only imports and tenant-scoped hunting table browsing."""

from pydantic import Field
from sqlalchemy import String, cast

from mockazure.hunting.access_models import HuntingSettings
from mockazure.hunting.authorization import ENTITLEMENTS
from mockazure.hunting.contracts import catalog
from mockazure.hunting.models import HuntingRecord
from mockazure.hunting.repository import ingest_rows
from mockazure.hunting.schemas import HuntingProvenance, validate_row
from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, Input, OperationDescriptor


class HuntingInput(Input):
    id: str = Field(min_length=1, max_length=512)
    record: dict


class HuntingSettingsInput(Input):
    id: str
    entitlements: list[str] = Field(max_length=6)


def settings_adapter():
    def normalize(payload):
        value = HuntingSettingsInput.model_validate(payload)
        if not set(value.entitlements) <= ENTITLEMENTS:
            raise ValueError("Unknown local hunting entitlement")
        return {"id": value.id, "entitlements": sorted(set(value.entitlements))}

    async def write(session, ctx, pending):
        for _, value in pending:
            if value["id"] != str(ctx.tenant_id):
                raise ValueError("Hunting settings must belong to the current tenant")
            row = await session.get(HuntingSettings, ctx.tenant_id)
            if row is None:
                row = HuntingSettings(tenant_id=ctx.tenant_id)
                session.add(row)
            row.entitlements = value["entitlements"]

    r = HuntingSettings
    payload = json_object(id=r.tenant_id, entitlements=r.entitlements)
    return PayloadAdapter(
        FamilyDescriptor(
            key="hunting.settings",
            service="Advanced hunting",
            api_path="/_mock/tenants/{tenant}/hunting/settings",
            display_fields=["id", "entitlements"],
            operations=[
                OperationDescriptor(
                    key="provision",
                    mode="fixture",
                    method="PUT",
                    api_version="local-fixture-v1",
                    path_template="/_mock/tenants/{tenant}/hunting/settings",
                    input_schema=HuntingSettingsInput.model_json_schema(),
                )
            ],
            contract_refs=["docs/hunting-data.md"],
            limitations=["Local service availability, not full Microsoft licensing compliance."],
        ),
        ReadSpec(r, r.tenant_id, payload, cast(payload, String)),
        normalize,
        write,
        replace_allowed=True,
    )


class HuntingAdapter(PayloadAdapter):
    def matches(self, existing, desired):
        return (
            existing["id"] == desired["id"]
            and existing["record"] == desired["record"]
            and existing["provenance"]
            == {
                "kind": "fixture",
                "source_ref": "management",
                "generator_version": "import-v1",
            }
        )


def adapters():
    result = [settings_adapter()]
    r = HuntingRecord
    for name, contract in catalog().items():

        def normalize(payload, name=name):
            value = HuntingInput.model_validate(payload)
            return {"id": value.id, "record": validate_row(name, value.record)}

        async def write(session, ctx, pending, name=name):
            await ingest_rows(
                session,
                ctx.tenant_id,
                name,
                [p["record"] for _, p in pending],
                [p["id"] for _, p in pending],
                HuntingProvenance(
                    kind="fixture", source_ref="management", generator_version="import-v1"
                ),
            )

        result.append(
            HuntingAdapter(
                FamilyDescriptor(
                    key="hunting." + name,
                    service="Advanced hunting",
                    api_path="/v1.0/security/runHuntingQuery",
                    display_fields=["id", "Timestamp", "provenance"],
                    operations=[
                        OperationDescriptor(
                            key="import",
                            mode="fixture",
                            method="POST",
                            path_template="/_mock/tenants/{tenant}/resources/hunting."
                            + name
                            + "/apply",
                            api_version="local-fixture-v1",
                            input_schema=HuntingInput.model_json_schema(),
                        )
                    ],
                    contract_refs=[f"src/mockazure/hunting/data/sources/{name}.html"],
                    limitations=[
                        "Typed fixture rows; imports never assert native sensor activity.",
                        "Omitted columns are nullable; availability is declared in the catalog.",
                        "Explicit source IDs are tenant/table scoped and cannot be overwritten.",
                    ],
                ),
                ReadSpec(
                    r,
                    r.source_id,
                    json_object(
                        id=r.source_id,
                        Timestamp=r.timestamp,
                        record=r.payload,
                        provenance=r.provenance,
                    ),
                    r.digest,
                    (r.table_name == name,),
                ),
                normalize,
                write,
            )
        )
    return result
