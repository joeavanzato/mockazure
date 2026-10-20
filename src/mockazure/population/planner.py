"""Read-only profile planning with bounded samples and expiring state preconditions."""

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator
from sqlalchemy import String, cast, func, select

from mockazure.admin.errors import AdminError
from mockazure.db.models import Tenant
from mockazure.population.activity_models import ActivityBatch
from mockazure.population.catalog import get_adapter, list_families
from mockazure.population.generators import VERSION, generate, inventory
from mockazure.population.models import (
    EntityBindingRow,
    OrganizationNamespace,
    PopulationPreview,
    PopulationRun,
    ResourceBinding,
)
from mockazure.population.protection import redact, seal, unseal
from mockazure.population.tokens import fingerprint
from mockazure.population.types import Input
from mockazure.profiles.resolver import resolve_template
from mockazure.tenants.schemas import TenantCreate


class Target(Input):
    existing_tenant_id: UUID | None = None
    create_tenant: TenantCreate | None = None

    @model_validator(mode="after")
    def exclusive(self):
        if (self.existing_tenant_id is None) == (self.create_tenant is None):
            raise ValueError("Select exactly one existing or new tenant target")
        return self


class RunRequest(Input):
    bundle_id: str = Field(default="builtin", max_length=64)
    template: str = Field(min_length=1, max_length=128)
    target: Target
    dataset_namespace: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    parameters: dict[str, JsonValue] = Field(default_factory=dict, max_length=256)
    identity_refs: dict[str, str] = Field(default_factory=dict, max_length=64)
    activity: ActivityBatch | None = None


class Preview(Input):
    id: UUID
    tenant_id: UUID
    digest: str
    resolved_digest: str
    state_digest: str
    expires_at: datetime
    counts: dict[str, int]
    samples: dict[str, list[dict]]
    warnings: list[str]


async def state_digest(session, tenant_id):
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        return fingerprint({"absent": str(tenant_id)})
    values = {
        "tenant": [
            tenant.slug,
            tenant.display_name,
            tenant.domain,
            tenant.licences,
            tenant.revision,
        ]
    }
    # Constant-memory SQL aggregates, including interior edits rather than merely max revision.
    for family in list_families():
        adapter = get_adapter(family.key)
        while not hasattr(adapter, "spec") and hasattr(adapter, "base"):
            adapter = adapter.base
        spec = getattr(adapter, "spec", None)
        if spec is None:
            continue
        row_hash = func.hashtextextended(
            func.concat(
                cast(spec.identifier, String),
                cast(spec.version, String),
                cast(spec.payload, String),
                cast(spec.parent, String) if spec.parent is not None else "",
            ),
            0,
        )
        row = (
            await session.execute(
                select(func.count(), func.bit_xor(row_hash))
                .select_from(spec.model)
                .where(spec.model.tenant_id == tenant_id, *spec.conditions)
            )
        ).one()
        values[family.key] = list(row)
    for model, columns in (
        (OrganizationNamespace, ["namespace", "generation"]),
        (
            EntityBindingRow,
            ["namespace", "logical_key", "service", "actual_id", "parent_id", "generation"],
        ),
    ):
        # JSON preserves field boundaries and nulls; SQL aggregation stays bounded.
        row_hash = func.hashtextextended(
            cast(func.jsonb_build_array(*(getattr(model, name) for name in columns)), String), 0
        )
        values[model.__tablename__] = list(
            (
                await session.execute(
                    select(func.count(), func.bit_xor(row_hash))
                    .select_from(model)
                    .where(model.tenant_id == tenant_id)
                )
            ).one()
        )
    return fingerprint(values)


async def bindings(session, tenant, namespace):
    rows = await session.scalars(
        select(ResourceBinding)
        .where(ResourceBinding.tenant_id == tenant, ResourceBinding.namespace == namespace)
        .limit(1025)
    )
    result = {row.key: row.actual_id for row in rows}
    if len(result) > 1024:
        raise ValueError("Namespace binding limit exceeded")
    return result


def parameters_for(snapshot, step):
    resolved = snapshot["resolved"]
    return {
        "tenant": snapshot["tenant"],
        "namespace": snapshot["request"]["dataset_namespace"],
        "users": resolved["parameters"].get("shared.users", 20),
        "days": resolved["parameters"].get("shared.days", 7),
        "seed": resolved["seed"],
        "referenceTime": resolved["referenceTime"],
        **step["parameters"],
    }


def resolve_refs(value, refs):
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            if value["$ref"] not in refs:
                raise ValueError("Resource dependency has no captured identity")
            return refs[value["$ref"]]
        return {k: resolve_refs(v, refs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_refs(v, refs) for v in value]
    return value


def mutations_for(snapshot, step, refs, offset, limit):
    from mockazure.population.activity import action_at, is_activity_step

    if is_activity_step(snapshot, step):
        total = snapshot["request"]["activity"]["action_count"]
        return [action_at(snapshot, i, refs) for i in range(offset, min(total, offset + limit))]
    params = resolve_refs(parameters_for(snapshot, step), refs)
    values = generate(params["generator"], params, refs, offset=offset, limit=limit)
    target = step.get("target")
    properties = snapshot["resolved"]["resources"].get(target, {}).get("properties", {})
    for index, mutation in enumerate(values):
        if mutation.operation != step["operation"]:
            raise ValueError("Generator operation differs from declared step")
        values[index] = mutation.model_copy(
            update={"payload": {**mutation.payload, **resolve_refs(properties, refs)}}
        )
    return values


async def preview(database, request: RunRequest, now: datetime):
    from mockazure.population.repository import load_bundle_version

    bundle, revision = await load_bundle_version(database, request.bundle_id)
    reference = now
    if request.target.existing_tenant_id:
        async with database.sessions() as session:
            previous = await session.scalar(
                select(PopulationRun)
                .where(
                    PopulationRun.tenant_id == request.target.existing_tenant_id,
                    PopulationRun.namespace == request.dataset_namespace,
                )
                .order_by(PopulationRun.created_at)
                .limit(1)
            )
            if previous:
                reference = datetime.fromisoformat(
                    unseal(database, previous.encrypted)["resolved"]["referenceTime"].replace(
                        "Z", "+00:00"
                    )
                )
    resolved = resolve_template(bundle, request.template, request.parameters, reference)
    if len(resolved.steps) > 1024 or len(resolved.resources) > 1024:
        raise ValueError("A run supports at most 1024 steps and bindings")
    tenant_id = request.target.existing_tenant_id or uuid4()
    async with database.sessions() as session:
        tenant = await session.get(Tenant, tenant_id)
        if request.target.existing_tenant_id and tenant is None:
            raise AdminError(404, "TenantNotFound", "Target tenant does not exist")
        domain = tenant.domain if tenant else request.target.create_tenant.domain
        state = await state_digest(session, tenant_id)
        organization = await session.get(
            OrganizationNamespace, (tenant_id, request.dataset_namespace)
        )
        refs = {
            "domain": domain,
            **await bindings(session, tenant_id, request.dataset_namespace),
            **request.identity_refs,
        }
    snapshot = {
        "request": request.model_dump(mode="json"),
        "resolved": resolved.model_dump(mode="json"),
        "tenant": str(tenant_id),
        "domain": domain,
        "bundle_revision": revision,
        "generator_version": VERSION,
        "generator_versions": {
            step.parameters.get("generator"): inventory()
            .get(step.parameters.get("generator"), {})
            .get("version")
            for step in resolved.steps.values()
        },
        "organization_generation": str(organization.generation if organization else uuid4())
        if any(
            step.parameters.get("generator", "").startswith("business.")
            for step in resolved.steps.values()
        )
        else None,
    }
    from mockazure.population.activity import VERSION as ACTIVITY_VERSION
    from mockazure.population.activity import step_count, validate_recipe

    snapshot["activity_version"] = ACTIVITY_VERSION if request.activity else None
    validate_recipe(snapshot)
    counts, samples = {}, {}
    for key, step in snapshot["resolved"]["steps"].items():
        generator = step["parameters"].get("generator")
        if generator not in inventory() or inventory()[generator]["family"] != step["family"]:
            raise ValueError("Step must select a compatible packaged generator")
        descriptor = get_adapter(step["family"]).describe()
        operation = next((o for o in descriptor.operations if o.key == step["operation"]), None)
        if operation is None or operation.mode != step["mode"]:
            raise ValueError("Step operation is not supported by the family")
        count = step_count(snapshot, key, step)
        if type(count) is not int or not 0 <= count <= 1000000:
            raise ValueError("Invalid generator count")
        counts[step["family"]] = counts.get(step["family"], 0) + count
        values = mutations_for(snapshot, step, refs, 0, min(max(count, 1), 10))
        for mutation in values:
            get_adapter(step["family"]).validate(mutation)
        family_samples = samples.setdefault(step["family"], [])
        family_samples.extend(
            redact(m.model_dump(mode="json")) for m in values[: 10 - len(family_samples)]
        )
        if step.get("target") and values:
            # Preview-only placeholders. Execution binds actual returned identifiers.
            value = values[0].payload
            identifier = value.get("id", value.get("Id", value.get("payload", {}).get("id")))
            identifier = (
                identifier
                or value.get("variables", {}).get("resource")
                or value.get("body", {}).get("TableName")
                or str(uuid4())
            )
            if step["family"] in {"content.objects", "monitor.workspaces"}:
                identifier = str(uuid4())
            refs.setdefault(step["target"], identifier)
    digest = fingerprint({"snapshot": snapshot, "state": state})
    row = PopulationPreview(
        id=uuid4(),
        tenant_id=tenant_id,
        digest=digest,
        state_digest=state,
        encrypted=seal(database, snapshot),
        expires_at=now + timedelta(hours=1),
    )
    async with database.sessions.begin() as session:
        session.add(row)
    return Preview(
        id=row.id,
        tenant_id=tenant_id,
        digest=digest,
        resolved_digest=resolved.digest,
        state_digest=state,
        expires_at=row.expires_at,
        counts=counts,
        samples=samples,
        warnings=[
            "Samples are illustrative; service-assigned identifiers are captured during apply.",
            "Public service actions require the declared test identity and permissions.",
            "A namespace is insert-only. Changed existing resources require explicit resource "
            "editing.",
        ],
    )
