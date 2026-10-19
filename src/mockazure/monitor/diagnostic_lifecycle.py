"""Administrative snapshots preserve captured deliveries without replaying imported facts."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import delete, select

from mockazure.admin.errors import AdminError
from mockazure.cloud.events import lock_cloud_state, validate_event
from mockazure.cloud.types import ServiceEvent
from mockazure.db.job_model import Job
from mockazure.identity.types import IdentityInput
from mockazure.monitor.diagnostic_models import DiagnosticDelivery, DiagnosticSetting
from mockazure.monitor.diagnostics import destination_caps, source_scope, validated_properties
from mockazure.monitor.publication import eligible
from mockazure.monitor.tables import instant


class SettingInput(IdentityInput):
    id: UUID
    resource_id: str
    name: str = Field(min_length=1, max_length=260)
    properties: dict


class DeliveryInput(IdentityInput):
    id: UUID
    setting_id: UUID
    source_id: UUID
    destination_kind: Literal["workspace", "storage"]
    destination: dict
    event: dict
    state: Literal["pending", "succeeded", "failed"]
    error_code: str | None = Field(default=None, max_length=80)
    result: dict | None = None
    claim_epoch: int = Field(ge=0)


class DiagnosticStateInput(IdentityInput):
    settings: list[SettingInput] = Field(default_factory=list, max_length=10000)
    deliveries: list[DeliveryInput] = Field(default_factory=list, max_length=100000)

    @model_validator(mode="after")
    def unique(self):
        if len({s.id for s in self.settings}) != len(self.settings) or len(
            {d.id for d in self.deliveries}
        ) != len(self.deliveries):
            raise ValueError("Duplicate diagnostic snapshot identity")
        identities = {(d.setting_id, d.source_id, d.destination_kind) for d in self.deliveries}
        if len(identities) != len(self.deliveries):
            raise ValueError("Duplicate captured diagnostic destination")
        return self


async def export_diagnostics(session, tenant):
    state = await lock_cloud_state(session, tenant)
    settings = list(
        await session.scalars(
            select(DiagnosticSetting)
            .where(DiagnosticSetting.tenant_id == tenant, DiagnosticSetting.deleted.is_(False))
            .order_by(DiagnosticSetting.id)
            .limit(10001)
        )
    )
    rows = list(
        await session.execute(
            select(DiagnosticDelivery, Job)
            .join(Job, Job.id == DiagnosticDelivery.id)
            .where(
                DiagnosticDelivery.tenant_id == tenant,
                DiagnosticDelivery.setting_id.in_([s.id for s in settings]),
            )
            .order_by(DiagnosticDelivery.id)
            .limit(100001)
        )
    )
    if len(settings) > 10000 or len(rows) > 100000:
        raise AdminError(413, "CloudStateLimit", "Diagnostic snapshot exceeds local limits")
    by_id = {s.id: s for s in settings}
    deliveries = []
    for delivery, job in rows:
        status = "pending" if job.state == "running" else job.state
        if status == "pending" and (
            delivery.generation != state.generation
            or not await eligible(session, delivery, by_id[delivery.setting_id])
        ):
            status = "failed"
        deliveries.append(
            DeliveryInput(
                id=delivery.id,
                setting_id=delivery.setting_id,
                source_id=delivery.source_id,
                destination_kind=delivery.destination_kind,
                destination=delivery.destination,
                event=delivery.event,
                state=status,
                error_code=job.error_code,
                result=job.result,
                claim_epoch=job.claim_epoch,
            )
        )
    return DiagnosticStateInput(
        settings=[
            SettingInput(id=s.id, resource_id=s.resource_id, name=s.name, properties=s.properties)
            for s in settings
        ],
        deliveries=deliveries,
    )


async def replace_diagnostics(session, tenant, value):
    from mockazure.providers.arm.errors import ArmError

    state = await lock_cloud_state(session, tenant)
    now = datetime.now(UTC)
    epochs = dict(
        (
            await session.execute(
                select(Job.id, Job.claim_epoch).where(
                    Job.tenant_id == tenant, Job.kind == "monitor.diagnostic"
                )
            )
        ).all()
    )
    async with session.begin_nested():
        await session.execute(
            delete(Job).where(Job.tenant_id == tenant, Job.kind == "monitor.diagnostic")
        )
        await session.execute(
            delete(DiagnosticSetting).where(DiagnosticSetting.tenant_id == tenant)
        )
        try:
            settings = {}
            counts = {}
            names = set()
            for item in value.settings:
                scope, service, categories = await source_scope(session, tenant, item.resource_id)
                properties = validated_properties(
                    {"properties": item.properties}, service, categories
                )
                key = scope.key, item.name.lower()
                if key in names or any(c in item.name for c in "/\\%?#"):
                    raise ValueError("Invalid or duplicate setting name")
                names.add(key)
                counts[scope.key] = counts.get(scope.key, 0) + 1
                if counts[scope.key] > 5:
                    raise ValueError("Too many settings")
                # The authenticated administrative restore explicitly authorizes
                # its owned local destinations; it cannot import cross-tenant caps.
                destinations = await destination_caps(
                    session, SimpleNamespace(tenant_id=tenant), properties, administrative=True
                )
                for destination in destinations:
                    destination["epoch"] = str(uuid4())
                existing = await session.get(DiagnosticSetting, item.id)
                if existing is not None:
                    raise ValueError("Diagnostic identity belongs to another tenant")
                row = DiagnosticSetting(
                    id=item.id,
                    tenant_id=tenant,
                    resource_key=scope.key,
                    resource_id=scope.display_id,
                    name=item.name,
                    name_key=item.name.lower(),
                    properties=properties,
                    destinations=destinations,
                    deleted=False,
                    updated_at=now,
                )
                session.add(row)
                settings[item.id] = row
            await session.flush()
            for item in value.deliveries:
                setting = settings.get(item.setting_id)
                if setting is None or await session.get(Job, item.id) is not None:
                    raise ValueError("Invalid delivery parent or identity")
                source = dict(item.event)
                source.update(
                    id=UUID(source["id"]),
                    tenant_id=UUID(source["tenant_id"]),
                    occurred_at=instant(source["occurred_at"]),
                )
                event = ServiceEvent(**source)
                validate_event(event)
                if event.tenant_id != tenant or event.id != item.source_id:
                    raise ValueError("Invalid diagnostic source identity")
                from mockazure.monitor.publication import TABLES
                from mockazure.providers.arm.scopes import parse_scope

                event_scope = parse_scope(event.resource_id)
                setting_scope = parse_scope(setting.resource_id)
                if event.service not in TABLES or (
                    event_scope.subscription_id != setting_scope.subscription_id
                    if event.service == "arm"
                    else event_scope.key != setting_scope.key
                ):
                    raise ValueError("Snapshot event does not belong to its diagnostic source")
                destination = next(
                    (
                        d
                        for d in setting.destinations
                        if d["kind"] == item.destination_kind
                        and d["resource"] == item.destination.get("resource")
                    ),
                    None,
                )
                if item.state == "pending":
                    if destination is None:
                        raise ValueError("Snapshot delivery destination is no longer configured")
                    destination = dict(destination)
                else:
                    # Terminal history is inert: preserve its original destination
                    # without granting it the capability of a current attachment.
                    from mockazure.providers.arm.models import Subscription

                    destination = dict(item.destination)
                    historical = parse_scope(destination["resource"])
                    subscription = await session.get(Subscription, historical.subscription_id)
                    expected_type = (
                        "microsoft.operationalinsights/workspaces"
                        if item.destination_kind == "workspace"
                        else "microsoft.storage/storageaccounts"
                    )
                    if (
                        subscription is None
                        or subscription.tenant_id != tenant
                        or "/".join(historical.resource_parts[1:3]).lower() != expected_type
                        or len(historical.resource_parts) != 4
                        or destination.get("kind") != item.destination_kind
                    ):
                        raise ValueError("Invalid terminal delivery destination")
                if item.destination_kind == "workspace":
                    mode = item.destination.get("mode")
                    if mode not in {"Dedicated", "AzureDiagnostics"}:
                        raise ValueError("Invalid captured table destination mode")
                    destination["mode"] = mode
                job = Job(
                    id=item.id,
                    tenant_id=tenant,
                    kind="monitor.diagnostic",
                    state=item.state,
                    payload={"delivery_id": str(item.id)},
                    available_at=now,
                    claim_epoch=max(item.claim_epoch, epochs.get(item.id, 0)) + 1,
                    result=item.result,
                    error_code=item.error_code,
                )
                session.add(job)
                await session.flush()
                session.add(
                    DiagnosticDelivery(
                        id=item.id,
                        tenant_id=tenant,
                        setting_id=item.setting_id,
                        source_id=item.source_id,
                        generation=state.generation,
                        destination_kind=item.destination_kind,
                        destination=destination,
                        event=item.event,
                    )
                )
            await session.flush()
        except (ValueError, TypeError, KeyError, ArmError):
            raise AdminError(
                422,
                "InvalidCloudState",
                "Diagnostic snapshot has invalid owned source/destination state",
            ) from None


async def prune_diagnostics(session, tenant, now, limit):
    if not 1 <= limit <= 1000 or now.tzinfo is None:
        raise ValueError("Invalid diagnostic cleanup bounds")
    # Completed delivery audit is retained 30 days; active work is never pruned.
    from datetime import timedelta

    ids = list(
        await session.scalars(
            select(Job.id)
            .where(
                Job.tenant_id == tenant,
                Job.kind == "monitor.diagnostic",
                Job.state.in_(("succeeded", "failed")),
                Job.updated_at < now - timedelta(days=30),
            )
            .order_by(Job.updated_at, Job.id)
            .limit(limit)
        )
    )
    if ids:
        await session.execute(delete(Job).where(Job.tenant_id == tenant, Job.id.in_(ids)))
    used = len(ids)
    if used < limit:
        retained = (
            select(DiagnosticDelivery.id)
            .where(DiagnosticDelivery.setting_id == DiagnosticSetting.id)
            .exists()
        )
        settings = list(
            await session.scalars(
                select(DiagnosticSetting.id)
                .where(
                    DiagnosticSetting.tenant_id == tenant,
                    DiagnosticSetting.deleted.is_(True),
                    DiagnosticSetting.updated_at < now - timedelta(days=30),
                    ~retained,
                )
                .order_by(DiagnosticSetting.updated_at, DiagnosticSetting.id)
                .limit(limit - used)
            )
        )
        if settings:
            await session.execute(
                delete(DiagnosticSetting).where(
                    DiagnosticSetting.tenant_id == tenant, DiagnosticSetting.id.in_(settings)
                )
            )
        used += len(settings)
    return used
