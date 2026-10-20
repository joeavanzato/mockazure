"""Namespace generations and service-specific identities, separate from legacy bindings."""

from uuid import UUID, uuid4

from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from mockazure.db.models import Tenant
from mockazure.population.models import EntityBindingRow, OrganizationNamespace
from mockazure.population.types import Input


class EntityBinding(Input):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    tenant_id: UUID
    namespace: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    logical_key: str = Field(pattern=r"^[A-Za-z0-9:._-]{1,128}$")
    service: str = Field(pattern=r"^[a-z][a-z0-9.-]{0,63}$")
    actual_id: str = Field(min_length=1, max_length=2048)
    parent_id: str | None = Field(default=None, min_length=1, max_length=2048)
    generation: UUID

    @field_validator("actual_id", "parent_id")
    @classmethod
    def printable(cls, value):
        if value is not None and any(ord(c) < 32 for c in value):
            raise ValueError("Binding identifier contains control characters")
        return value


def view(row):
    return EntityBinding(
        tenant_id=row.tenant_id,
        namespace=row.namespace,
        logical_key=row.logical_key,
        service=row.service,
        actual_id=row.actual_id,
        parent_id=row.parent_id,
        generation=row.generation,
    )


async def bind(session, binding: EntityBinding) -> EntityBinding:
    # Revalidate even trusted callers' model_copy(update=...), which skips Pydantic validation.
    binding = EntityBinding.model_validate(binding.model_dump())
    if await session.get(Tenant, binding.tenant_id) is None:
        raise ValueError("Unknown binding tenant")
    await session.execute(
        insert(OrganizationNamespace)
        .values(
            tenant_id=binding.tenant_id,
            namespace=binding.namespace,
            generation=binding.generation,
        )
        .on_conflict_do_nothing()
    )
    state = await session.get(
        OrganizationNamespace,
        (binding.tenant_id, binding.namespace),
        with_for_update=True,
        populate_existing=True,
    )
    if state.generation != binding.generation:
        raise ValueError("Binding generation is stale")
    if binding.parent_id is not None:
        parent = await session.scalar(
            select(EntityBindingRow)
            .where(
                EntityBindingRow.tenant_id == binding.tenant_id,
                EntityBindingRow.namespace == binding.namespace,
                EntityBindingRow.service == binding.service,
                EntityBindingRow.generation == binding.generation,
                func.md5(EntityBindingRow.actual_id) == func.md5(binding.parent_id),
                EntityBindingRow.actual_id == binding.parent_id,
            )
            .limit(1)
        )
        if parent is None or binding.parent_id == binding.actual_id:
            raise ValueError("Missing or invalid binding parent")
    key = (binding.tenant_id, binding.namespace, binding.logical_key, binding.service)
    row = await session.get(EntityBindingRow, key, populate_existing=True)
    if row is not None and row.generation == state.generation:
        if view(row) != binding:
            raise ValueError("Binding conflict; cannot retarget an existing namespace")
        return view(row)
    if row is None:
        row = EntityBindingRow(**binding.model_dump())
        session.add(row)
    else:
        row.actual_id, row.parent_id, row.generation = (
            binding.actual_id,
            binding.parent_id,
            binding.generation,
        )
    await session.flush()
    return view(row)


async def resolve(
    session, tenant_id: UUID, namespace: str, logical_key: str, service: str
) -> EntityBinding:
    result = (
        await session.execute(
            select(EntityBindingRow, OrganizationNamespace.generation)
            .join(
                OrganizationNamespace,
                (
                    (OrganizationNamespace.tenant_id == EntityBindingRow.tenant_id)
                    & (OrganizationNamespace.namespace == EntityBindingRow.namespace)
                ),
            )
            .where(
                EntityBindingRow.tenant_id == tenant_id,
                EntityBindingRow.namespace == namespace,
                EntityBindingRow.logical_key == logical_key,
                EntityBindingRow.service == service,
            )
            .execution_options(populate_existing=True)
        )
    ).first()
    if result is None:
        raise ValueError("Missing service binding")
    row, generation = result
    if row.generation != generation:
        raise ValueError("Binding generation is stale")
    return view(row)


async def retire_namespace(
    session, tenant_id: UUID, namespace: str, *, expected_generation: UUID
) -> UUID:
    state = await session.get(
        OrganizationNamespace,
        (tenant_id, namespace),
        with_for_update=True,
        populate_existing=True,
    )
    if state is None or state.generation != expected_generation:
        raise ValueError("Namespace generation is missing or stale")
    state.generation = uuid4()
    await session.flush()
    return state.generation
