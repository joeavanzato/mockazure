"""Tenant-serialized seat accounting and direct-assignment public revisions."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.directory.history import record_change, reserve_revision
from mockazure.directory.models import DirectoryState
from mockazure.directory.mutations import AdminActor, DirectoryCommand
from mockazure.directory.repository import public_object
from mockazure.directory.types import DirectoryChange
from mockazure.evidence.directory_facts import capture_directory_facts
from mockazure.identity.models import DirectoryRole, User
from mockazure.licensing.catalog import COUNTRIES, P1_PLAN, P2_PLAN
from mockazure.licensing.models import SubscribedSku, UserLicense
from mockazure.licensing.schemas import Entitlements, SkuInput, SkuView
from mockazure.providers.graph.body import invalid


async def lock_directory(session, tenant_id):
    await session.execute(
        insert(DirectoryState)
        .values(tenant_id=tenant_id, revision=0, generation=uuid4())
        .on_conflict_do_nothing(index_elements=["tenant_id"])
    )
    await session.scalar(
        select(DirectoryState).where(DirectoryState.tenant_id == tenant_id).with_for_update()
    )


def sku_view(row):
    return SkuView(**{key: getattr(row, key) for key in SkuView.model_fields})


async def inventory(session, tenant_id):
    return list(
        await session.scalars(
            select(SubscribedSku)
            .where(SubscribedSku.tenant_id == tenant_id)
            .order_by(SubscribedSku.sku_id)
        )
    )


async def upsert_sku(session, tenant_id, value: SkuInput) -> SkuView:
    if await session.get(Tenant, tenant_id) is None:
        raise AdminError(404, "TenantNotFound", "Tenant not found")
    await lock_directory(session, tenant_id)
    row = await session.get(SubscribedSku, (tenant_id, value.sku_id), with_for_update=True)
    rows = await inventory(session, tenant_id)
    if row is None and len(rows) >= 100:
        raise AdminError(409, "InventoryLimit", "At most100 subscribed SKUs are supported")
    if row and value.enabled < row.consumed_units:
        raise AdminError(409, "SeatsInUse", "Capacity cannot be lower than current assignments")
    assignments = list(
        await session.scalars(
            select(UserLicense).where(
                UserLicense.tenant_id == tenant_id, UserLicense.sku_id == value.sku_id
            )
        )
    )
    plans = {str(p.servicePlanId) for p in value.service_plans}
    if any(not set(a.disabled_plans) <= plans for a in assignments):
        raise AdminError(409, "PlanInUse", "A disabled assignment references a removed plan")
    if value.allowed_usage_locations and assignments:
        locations = list(
            await session.scalars(
                select(User.profile["usageLocation"].as_string()).where(
                    User.tenant_id == tenant_id, User.id.in_([a.user_id for a in assignments])
                )
            )
        )
        if any(location not in value.allowed_usage_locations for location in locations):
            raise AdminError(
                409, "LocationInUse", "Inventory restriction conflicts with an assigned user"
            )
    if row is None:
        row = SubscribedSku(
            tenant_id=tenant_id,
            sku_id=value.sku_id,
            id=uuid5(tenant_id, "subscribedSku:" + str(value.sku_id)),
            consumed_units=0,
        )
        session.add(row)
    for name, field in value.model_dump(mode="json", exclude={"sku_id"}).items():
        setattr(row, name, field)
    await session.flush()
    # Inventory policy changes alter effective assigned plans and public deltas in the same commit.
    for uid in sorted({a.user_id for a in assignments}):
        user = await session.scalar(
            select(User)
            .where(User.tenant_id == tenant_id, User.id == uid)
            .execution_options(populate_existing=True)
        )
        if user and not user.deleted_at:
            await update_public_assignment(
                session, user, AdminActor("licensing_inventory"), datetime.now(UTC), uuid4()
            )
    return sku_view(row)


async def assignment_rows(session, tenant_id, user_id):
    return (
        await session.execute(
            select(UserLicense, SubscribedSku)
            .join(
                SubscribedSku,
                (SubscribedSku.tenant_id == UserLicense.tenant_id)
                & (SubscribedSku.sku_id == UserLicense.sku_id),
            )
            .where(UserLicense.tenant_id == tenant_id, UserLicense.user_id == user_id)
            .order_by(UserLicense.sku_id)
        )
    ).all()


def plan_status(sku, plan, disabled):
    if plan["servicePlanId"] in disabled:
        return "Deleted"
    if sku.status != "Enabled":
        return sku.status
    return "Enabled" if plan["provisioningStatus"] == "Success" else None


async def public_assignments(session, tenant_id, user_id):
    licenses, plans = [], {}
    for assignment, sku in await assignment_rows(session, tenant_id, user_id):
        licenses.append({"skuId": str(sku.sku_id), "disabledPlans": assignment.disabled_plans})
        for plan in sku.service_plans:
            value = {
                "servicePlanId": plan["servicePlanId"],
                "service": None,
                "assignedDateTime": assignment.assigned_at.isoformat(),
                "capabilityStatus": plan_status(sku, plan, assignment.disabled_plans),
            }
            prior = plans.get(plan["servicePlanId"])
            if (
                prior is None
                or (
                    value["capabilityStatus"] == "Enabled"
                    and prior["capabilityStatus"] != "Enabled"
                )
                or (
                    value["capabilityStatus"] == prior["capabilityStatus"]
                    and value["assignedDateTime"] < prior["assignedDateTime"]
                )
            ):
                plans[plan["servicePlanId"]] = value
    return licenses, [plans[key] for key in sorted(plans)]


async def update_public_assignment(session, user, actor, now, correlation_id):
    before = public_object(user)
    licenses, plans = await public_assignments(session, user.tenant_id, user.id)
    user.profile = {**user.profile, "assignedLicenses": licenses, "assignedPlans": plans}
    after = public_object(user)
    changed = frozenset(
        k
        for k in ("assignedLicenses", "assignedPlans")
        if before.properties.get(k) != after.properties.get(k)
    )
    if not changed:
        return after
    revision = await reserve_revision(session, user.tenant_id)
    command = DirectoryCommand("user", "license", user.id, {})
    facts = await capture_directory_facts(
        session, actor, command, before, after, correlation_id, []
    )
    await record_change(
        session,
        DirectoryChange(revision, "user", user.id, changed),
        before,
        after,
        facts,
        occurred_at=now,
    )
    return after


async def assign_licences(session, actor, user_id, command, now, correlation_id):
    from mockazure.providers.graph.licensing import authorize_licensing

    await authorize_licensing(session, actor, "assign", user_id)
    tenant_id = actor.tenant_id
    await lock_directory(session, tenant_id)
    user = await session.scalar(
        select(User)
        .where(User.tenant_id == tenant_id, User.id == user_id, User.deleted_at.is_(None))
        .execution_options(populate_existing=True)
    )
    if user is None:
        from mockazure.providers.graph.directory import not_found

        not_found()
    if user.profile.get("userType", "Member") != "Member" or await session.scalar(
        select(DirectoryRole.role)
        .where(DirectoryRole.tenant_id == tenant_id, DirectoryRole.user_id == user_id)
        .limit(1)
    ):
        invalid("Assignments to privileged or non-Member targets are outside this profile")
    desired = {item.skuId: item for item in command.addLicenses}
    requested = set(desired) | set(command.removeLicenses)
    skus = list(
        await session.scalars(
            select(SubscribedSku)
            .where(SubscribedSku.tenant_id == tenant_id, SubscribedSku.sku_id.in_(requested))
            .order_by(SubscribedSku.sku_id)
            .with_for_update()
        )
    )
    by_id = {s.sku_id: s for s in skus}
    if set(by_id) != requested:
        invalid("Unknown subscribed SKU")
    existing = {a.sku_id: a for a, _ in await assignment_rows(session, tenant_id, user_id)}
    location = user.profile.get("usageLocation")
    for key, add in desired.items():
        sku = by_id[key]
        if location not in COUNTRIES or (
            sku.allowed_usage_locations and location not in sku.allowed_usage_locations
        ):
            invalid("A supported usageLocation is required for this SKU")
        if sku.status != "Enabled" or sku.enabled <= 0:
            invalid("Subscribed SKU is not enabled")
        if not set(map(str, add.disabledPlans)) <= {p["servicePlanId"] for p in sku.service_plans}:
            invalid("Unknown disabled service plan")
        if key not in existing and sku.consumed_units >= sku.enabled:
            invalid("No available licence seats")
    for key in command.removeLicenses:
        if key in existing:
            by_id[key].consumed_units -= 1
            await session.delete(existing[key])
    for key, add in desired.items():
        disabled = list(map(str, add.disabledPlans))
        if key in existing:
            existing[key].disabled_plans = disabled
        else:
            by_id[key].consumed_units += 1
            session.add(
                UserLicense(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    sku_id=key,
                    disabled_plans=disabled,
                    assigned_at=now,
                )
            )
    await session.flush()
    return await update_public_assignment(session, user, actor, now, correlation_id)


async def release_user(session, tenant_id, user_id):
    rows = await assignment_rows(session, tenant_id, user_id)
    if not rows:
        return
    skus = list(
        await session.scalars(
            select(SubscribedSku)
            .where(
                SubscribedSku.tenant_id == tenant_id,
                SubscribedSku.sku_id.in_([a.sku_id for a, _ in rows]),
            )
            .order_by(SubscribedSku.sku_id)
            .with_for_update()
        )
    )
    for sku in skus:
        sku.consumed_units -= 1
    await session.execute(
        delete(UserLicense).where(
            UserLicense.tenant_id == tenant_id, UserLicense.user_id == user_id
        )
    )
    await session.flush()


async def entitlements(session, tenant_id, user_id) -> Entitlements:
    skus = await inventory(session, tenant_id)
    available = set()
    for sku in skus:
        if sku.status == "Enabled" and sku.enabled > 0:
            available.update(
                UUID(p["servicePlanId"])
                for p in sku.service_plans
                if p["provisioningStatus"] == "Success"
            )
    effective = set()
    if user_id is not None:
        for assignment, sku in await assignment_rows(session, tenant_id, user_id):
            if sku.status == "Enabled" and sku.enabled > 0:
                effective.update(
                    UUID(p["servicePlanId"])
                    for p in sku.service_plans
                    if p["provisioningStatus"] == "Success"
                    and p["servicePlanId"] not in assignment.disabled_plans
                )
    tier = "P2" if P2_PLAN in available else ("P1" if P1_PLAN in available else "Free")
    digest = hashlib.sha256(
        json.dumps(
            [tier, sorted(map(str, available)), sorted(map(str, effective))], separators=(",", ":")
        ).encode()
    ).hexdigest()
    return Entitlements(tier, frozenset(effective), digest)
