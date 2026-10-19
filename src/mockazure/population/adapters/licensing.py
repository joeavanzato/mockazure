"""Explicit local subscribed SKU inventory; public license assignment stays separate."""

from sqlalchemy import String, cast

from mockazure.licensing.models import SubscribedSku
from mockazure.licensing.schemas import SkuDefinition, SkuInput
from mockazure.licensing.service import upsert_sku
from mockazure.population.adapters.arm import json_object
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, OperationDescriptor


def adapters():
    def normalize(payload):
        value = SkuInput.model_validate(
            {"sku_id": payload["id"], **{k: v for k, v in payload.items() if k != "id"}}
        )
        return {"id": str(value.sku_id), **value.model_dump(mode="json", exclude={"sku_id"})}

    async def write(session, ctx, pending):
        for _, payload in pending:
            await upsert_sku(
                session,
                ctx.tenant_id,
                SkuInput.model_validate(
                    {"sku_id": payload["id"], **{k: v for k, v in payload.items() if k != "id"}}
                ),
            )

    r = SubscribedSku
    payload = json_object(
        id=r.sku_id, **{name: getattr(r, name) for name in SkuDefinition.model_fields}
    )
    return [
        PayloadAdapter(
            FamilyDescriptor(
                key="identity.skus",
                service="Identity and permissions",
                api_path="/v1.0/subscribedSkus",
                display_fields=["id", "sku_part_number", "enabled", "status"],
                operations=[
                    OperationDescriptor(
                        key="provision",
                        mode="fixture",
                        method="PUT",
                        api_version="local-fixture-v1",
                        path_template="/_mock/tenants/{tenant}/licensing/skus/{id}",
                        input_schema=SkuDefinition.model_json_schema(),
                    )
                ],
                contract_refs=["docs/licensing.md"],
                limitations=[
                    "Explicit tenant SKU capacity, validated service plans and seat limits. ID "
                    "is the SKU GUID."
                ],
            ),
            ReadSpec(r, r.sku_id, payload, cast(payload, String)),
            normalize,
            write,
            replace_allowed=True,
        )
    ]
