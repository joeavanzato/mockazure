"""Explicit owned-device fixtures; compliance remains a separate Intune observation."""

from uuid import UUID

from pydantic import Field
from sqlalchemy import String, cast, func, select

from mockazure.admin.errors import AdminError
from mockazure.identity.models import User
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, Input, OperationDescriptor
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.models import DeviceUser, SecurityObject, SecurityOwner
from mockazure.security.repository import lock_security


class OwnedDevice(Input):
    payload: dict = Field()
    owner_id: UUID


class DeviceAdapter(PayloadAdapter):
    async def matches_mutation(self, session, ctx, old, mutation, value):
        if not await super().matches_mutation(session, ctx, old, mutation, value):
            return False
        return (
            await session.get(
                SecurityOwner, (ctx.tenant_id, UUID(old.id), UUID(mutation.payload["owner_id"]))
            )
            is not None
        )


def adapters():
    def normalize(value):
        data = OwnedDevice.model_validate(value)
        validate_payload("v1.0", "device", data.payload)
        UUID(data.payload["id"])
        UUID(data.payload["deviceId"])
        return data.payload

    async def write(session, ctx, pending):
        await lock_security(session, ctx.tenant_id)
        for mutation, body in pending:
            owner = await session.get(User, UUID(mutation.payload["owner_id"]))
            if owner is None or owner.tenant_id != ctx.tenant_id or owner.deleted_at is not None:
                raise AdminError(422, "MissingOwner", "Device owner must be a current local user")
            identifier = UUID(body["id"])
            duplicate = await session.scalar(
                select(SecurityObject.id)
                .where(
                    SecurityObject.tenant_id == ctx.tenant_id,
                    SecurityObject.kind == "device",
                    func.lower(SecurityObject.properties["deviceId"].as_string())
                    == body["deviceId"].lower(),
                )
                .limit(1)
            )
            if duplicate is not None:
                raise AdminError(
                    409, "DuplicateDeviceId", "Device alternate identity already exists"
                )
            session.add(
                SecurityObject(
                    tenant_id=ctx.tenant_id, id=identifier, kind="device", properties=body
                )
            )
            await session.flush()
            session.add(
                SecurityOwner(tenant_id=ctx.tenant_id, object_id=identifier, owner_id=owner.id)
            )
            session.add(DeviceUser(tenant_id=ctx.tenant_id, device_id=identifier, user_id=owner.id))

    r = SecurityObject
    return [
        DeviceAdapter(
            FamilyDescriptor(
                key="directory.devices",
                service="Entra",
                api_path="/v1.0/devices",
                display_fields=["id", "displayName", "deviceId", "operatingSystem"],
                operations=[
                    OperationDescriptor(
                        key="import-owned",
                        mode="fixture",
                        method="POST",
                        api_version="local-fixture-v1",
                        path_template="/_mock/tenants/{tenant}/resources/directory.devices/apply",
                        input_schema=OwnedDevice.model_json_schema(),
                    )
                ],
                contract_refs=["integrations/contracts/graph-security.md"],
                limitations=[
                    "Explicit local fixture; registered owner/user are directory relationships. "
                    "No device execution or compliance inference."
                ],
            ),
            ReadSpec(
                r, r.id, r.properties, func.md5(cast(r.properties, String)), (r.kind == "device",)
            ),
            normalize,
            write,
        )
    ]
