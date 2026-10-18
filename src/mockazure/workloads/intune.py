"""Typed Intune observations; policy assignments never become compliance evidence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select

from mockazure.authorization.evaluator import AuthorizationDenied, require_current_identity
from mockazure.licensing.service import inventory
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import validate_payload
from mockazure.security.models import SecurityObject
from mockazure.workloads.intune_contracts import (
    CONFIGURATIONS,
    INTUNE_PLAN,
    KINDS,
    POLICIES,
    SCOPE,
    DeviceObservation,
)
from mockazure.workloads.models import WorkloadVersion as V


def validate_intune_record(kind, payload):
    if kind not in KINDS:
        raise ValueError("Unsupported Intune evidence kind")
    if kind in {"deviceConfiguration", "deviceCompliancePolicy"}:
        allowed = CONFIGURATIONS if kind == "deviceConfiguration" else POLICIES
        annotation = payload.get("@odata.type")
        if not isinstance(annotation, str):
            raise ValueError("An Intune platform type annotation must be a string")
        derived = annotation.removeprefix("#microsoft.graph.")
        if derived not in allowed:
            raise ValueError("An explicitly supported Intune platform type is required")
    validate_payload("v1.0", kind, payload)
    if kind == "managedDevice":
        for field in ("azureADDeviceId", "userId"):
            if payload.get(field):
                payload[field] = str(UUID(payload[field]))


async def licensed(session, tenant):
    return any(
        row.status == "Enabled"
        and row.enabled > 0
        and any(
            plan.get("servicePlanId") == INTUNE_PLAN and plan.get("provisioningStatus") == "Success"
            for plan in row.service_plans
        )
        for row in await inventory(session, tenant)
    )


async def authorize_intune(session, actor, kind):
    try:
        await require_current_identity(actor, session)
    except AuthorizationDenied:
        raise GraphError(
            403, "Forbidden", "Current application or user identity is required"
        ) from None
    family = (
        "DeviceManagementManagedDevices"
        if kind in {"managedDevice", "detectedApp"}
        else "DeviceManagementConfiguration"
    )
    if not actor.permissions & {family + ".Read.All", family + ".ReadWrite.All"}:
        raise GraphError(403, "Forbidden", "The Intune operation permission is required")
    if not await licensed(session, actor.tenant_id):
        raise GraphError(403, "Forbidden", "An active tenant Intune entitlement is required")


async def device_observation(
    session, tenant_id: UUID, directory_device_id: UUID
) -> DeviceObservation | None:
    if not await licensed(session, tenant_id):
        return None
    devices = list(
        await session.scalars(
            select(SecurityObject)
            .where(
                SecurityObject.tenant_id == tenant_id,
                SecurityObject.kind == "device",
                func.lower(SecurityObject.properties["deviceId"].as_string())
                == str(directory_device_id),
            )
            .limit(2)
        )
    )
    if len(devices) != 1 or devices[0].properties.get("accountEnabled") is not True:
        return None
    rows = list(
        await session.scalars(
            select(V)
            .where(
                V.tenant_id == tenant_id,
                V.workload == "intune",
                V.scope == SCOPE,
                V.kind == "managedDevice",
                V.valid_to.is_(None),
                V.deleted.is_(False),
                func.lower(V.payload["azureADDeviceId"].as_string()) == str(directory_device_id),
            )
            .limit(2)
        )
    )
    if len(rows) != 1:
        return None
    payload = rows[0].payload
    return DeviceObservation(
        directory_device_id,
        payload.get("operatingSystem"),
        {"compliant": True, "noncompliant": False}.get(payload["complianceState"]),
        datetime.fromisoformat(payload["lastSyncDateTime"].replace("Z", "+00:00")),
    )


async def validate_intune_state(session, tenant, incoming, deleted, keys, edges):
    from mockazure.workloads.intune_contracts import ROOTS

    roots = set(ROOTS.values())
    parents = {}
    for source, relation, target in edges:
        if target[0] not in roots:
            if target in parents:
                raise ValueError("Intune child evidence requires one policy parent")
            parents[target] = source
    if any(key[0] not in roots and key not in parents for key in keys):
        raise ValueError("Intune policy child evidence is orphaned")
    replacements = {key[1] for key in incoming.keys() | deleted if key[0] == "managedDevice"}
    mappings = list(
        await session.scalars(
            select(V.payload["azureADDeviceId"].as_string()).where(
                V.tenant_id == tenant,
                V.workload == "intune",
                V.kind == "managedDevice",
                V.valid_to.is_(None),
                V.deleted.is_(False),
                V.id.not_in(replacements),
            )
        )
    )
    mappings.extend(
        record.payload.get("azureADDeviceId")
        for key, record in incoming.items()
        if key[0] == "managedDevice"
    )
    known = [str(UUID(value)) for value in mappings if value and UUID(value).int]
    if len(known) != len(set(known)):
        raise ValueError("Ambiguous managed-device mapping to directory deviceId")
    if known:
        directory_id = func.lower(SecurityObject.properties["deviceId"].as_string())
        ambiguous = await session.scalar(
            select(directory_id)
            .where(
                SecurityObject.tenant_id == tenant,
                SecurityObject.kind == "device",
                directory_id.in_(known),
            )
            .group_by(directory_id)
            .having(func.count() > 1)
            .limit(1)
        )
        if ambiguous:
            raise ValueError("Ambiguous canonical directory deviceId")
