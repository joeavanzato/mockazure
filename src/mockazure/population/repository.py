"""Transactional run submission, immutable bundles and bounded report views."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from mockazure.admin.errors import AdminError
from mockazure.db.job_model import Job
from mockazure.db.models import Tenant
from mockazure.population.models import (
    ACTIVE,
    BundleHead,
    BundleVersion,
    OrganizationNamespace,
    PopulationPreview,
    PopulationRun,
    PopulationStep,
    RunView,
)
from mockazure.population.protection import seal, unseal
from mockazure.population.tokens import fingerprint
from mockazure.profiles.builtin import builtin_bundle
from mockazure.profiles.loader import load_bundle


async def save_bundle(database, bundle, *, bundle_id=None, expected_revision=None):
    import json

    # Reuse bounded safe parsing even for model instances constructed by Python callers.
    bundle = load_bundle(
        {
            key: json.dumps(value.model_dump(mode="json", exclude_unset=True)).encode()
            for key, value in bundle.documents.items()
        }
    )
    async with database.sessions.begin() as session:
        if bundle_id is None:
            head = BundleHead(id=uuid4(), revision=1)
            session.add(head)
            await session.flush()
        else:
            head = await session.get(BundleHead, UUID(str(bundle_id)), with_for_update=True)
            if head is None:
                raise AdminError(404, "BundleNotFound", "Profile bundle does not exist")
            if head.revision != expected_revision:
                raise AdminError(409, "BundleConflict", "Profile revision changed")
            head.revision += 1
        data = {
            "documents": {
                k: v.model_dump(mode="json", exclude_unset=True)
                for k, v in bundle.documents.items()
            }
        }
        digest = fingerprint(data)
        session.add(
            BundleVersion(
                id=head.id, revision=head.revision, digest=digest, encrypted=seal(database, data)
            )
        )
        return {"id": head.id, "revision": head.revision, "digest": digest}


async def load_bundle_version(database, identifier, revision=None):
    import json

    if identifier == "builtin":
        return builtin_bundle(), 1
    async with database.sessions() as session:
        head = await session.get(BundleHead, UUID(str(identifier)))
        row = (
            await session.get(BundleVersion, (head.id, revision or head.revision)) if head else None
        )
        if row is None:
            raise AdminError(404, "BundleNotFound", "Profile bundle revision does not exist")
        data = unseal(database, row.encrypted)
        return load_bundle(
            {k: json.dumps(v).encode() for k, v in data["documents"].items()}
        ), row.revision


async def submit_run(database, preview_id, digest, now):
    from mockazure.population.planner import state_digest

    async with database.sessions.begin() as session:
        p = await session.get(PopulationPreview, preview_id, with_for_update=True)
        if p is None or p.digest != digest:
            raise AdminError(409, "PreviewConflict", "Preview identity or digest does not match")
        if p.run_id:
            return await view_session(session, p.run_id)
        if p.expires_at <= now:
            raise AdminError(409, "PreviewExpired", "Create a fresh preview")
        # Tenant row lock serializes competing submit/resume operations without spanning HTTP.
        tenant = await session.get(Tenant, p.tenant_id, with_for_update=True)
        snapshot = unseal(database, p.encrypted)
        if await state_digest(session, p.tenant_id) != p.state_digest:
            raise AdminError(409, "PreviewStale", "Target state changed; create a fresh preview")
        if tenant is None:
            data = snapshot["request"]["target"]["create_tenant"]
            if not data:
                raise AdminError(409, "TenantMissing", "Target tenant was removed")
            conflict = await session.scalar(
                select(Tenant.id).where(
                    (Tenant.slug == data["slug"]) | (Tenant.domain == data["domain"])
                )
            )
            if conflict:
                raise AdminError(409, "TenantConflict", "Tenant slug or domain is already in use")
            tenant = Tenant(id=p.tenant_id, **data)
            session.add(tenant)
            await session.flush()
        await ensure_slot(session, p.tenant_id)
        if snapshot.get("organization_generation"):
            namespace = snapshot["request"]["dataset_namespace"]
            organization = await session.get(OrganizationNamespace, (p.tenant_id, namespace))
            generation = UUID(snapshot["organization_generation"])
            if organization is None:
                session.add(
                    OrganizationNamespace(
                        tenant_id=p.tenant_id, namespace=namespace, generation=generation
                    )
                )
            elif organization.generation != generation:
                raise AdminError(409, "NamespaceGenerationChanged", "Namespace generation changed")
        identifier, job_id = uuid4(), uuid4()
        session.add(
            Job(
                id=job_id,
                tenant_id=p.tenant_id,
                kind="population.apply",
                payload={"run_id": str(identifier)},
                available_at=now,
            )
        )
        await session.flush()
        row = PopulationRun(
            id=identifier,
            job_id=job_id,
            tenant_id=p.tenant_id,
            namespace=snapshot["request"]["dataset_namespace"],
            state="pending",
            resolved_digest=snapshot["resolved"]["digest"],
            encrypted=p.encrypted,
            progress={"created": 0, "updated": 0, "unchanged": 0, "verified": 0},
            errors=[],
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        await session.flush()
        for ordinal, (key, step) in enumerate(snapshot["resolved"]["steps"].items()):
            session.add(
                PopulationStep(
                    run_id=identifier,
                    ordinal=ordinal,
                    key=key,
                    family=step["family"],
                    count=step["parameters"].get("count", 1),
                    offset=0,
                    state="pending",
                )
            )
        p.run_id = identifier
        await session.flush()
        return await view_session(session, identifier)


async def ensure_slot(session, tenant, excluding=None):
    query = select(PopulationRun.id).where(
        PopulationRun.tenant_id == tenant, PopulationRun.state.in_(ACTIVE)
    )
    if excluding:
        query = query.where(PopulationRun.id != excluding)
    if await session.scalar(query.limit(1)):
        raise AdminError(409, "TenantBusy", "Another population run is active for this tenant")


async def view_session(session, run_id):
    row = await session.get(PopulationRun, run_id)
    if row is None:
        raise AdminError(404, "RunNotFound", "Population run does not exist")
    total = await session.scalar(
        select(func.count()).select_from(PopulationStep).where(PopulationStep.run_id == run_id)
    )
    return RunView(
        id=row.id,
        tenant_id=row.tenant_id,
        state=row.state,
        progress=row.progress,
        steps={"completed": row.cursor, "total": total},
        errors=row.errors,
        manifest_digest=row.resolved_digest,
    )


async def run_view(database, run_id):
    async with database.sessions() as session:
        return await view_session(session, run_id)


async def cancel_run(database, run_id):
    async with database.sessions.begin() as session:
        row = await session.get(PopulationRun, run_id)
        if row is None:
            raise AdminError(404, "RunNotFound", "Population run does not exist")
        # Same job→run lock order as worker checkpoints.
        job = await session.get(Job, row.job_id, with_for_update=True)
        row = await session.get(PopulationRun, run_id, with_for_update=True, populate_existing=True)
        if row.state in ACTIVE:
            row.cancel_requested = True
            if job.state != "running":
                row.state, job.state = "cancelled", "succeeded"
        await session.flush()
        return await view_session(session, run_id)


async def resume_run(database, run_id):
    async with database.sessions.begin() as session:
        row = await session.get(PopulationRun, run_id)
        if row is None:
            raise AdminError(404, "RunNotFound", "Population run does not exist")
        await session.get(Tenant, row.tenant_id, with_for_update=True)
        job = await session.get(Job, row.job_id, with_for_update=True)
        row = await session.get(PopulationRun, run_id, with_for_update=True, populate_existing=True)
        if row.state in ACTIVE:
            return await view_session(session, run_id)
        await ensure_slot(session, row.tenant_id, row.id)
        if job.state == "running" and job.lease_until and job.lease_until > datetime.now(UTC):
            raise AdminError(
                409, "WorkerActive", "The current worker is still completing its checkpoint"
            )
        row.cursor, row.cancel_requested, row.state, row.errors = 0, False, "pending", []
        row.progress = {"created": 0, "updated": 0, "unchanged": 0, "verified": 0}
        await session.execute(
            update(PopulationStep)
            .where(PopulationStep.run_id == run_id)
            .values(offset=0, state="pending")
        )
        job.state, job.attempts, job.available_at = "pending", 0, datetime.now(UTC)
        job.claim_epoch += 1
        job.lease_owner = job.lease_until = None
        await session.flush()
        return await view_session(session, run_id)


async def export_manifest(database, run_id):
    async with database.sessions() as session:
        view = await view_session(session, run_id)
        row = await session.get(PopulationRun, run_id)
        snapshot = unseal(database, row.encrypted)
        resolved = snapshot["resolved"]
        return {
            "schema_version": 1,
            "run": view.model_dump(mode="json"),
            "source_digest": resolved["digest"],
            "template": resolved["template"],
            "namespace": row.namespace,
            "generator_version": snapshot["generator_version"],
            "reference_time": resolved["referenceTime"],
            "seed": resolved["seed"],
            "steps": [
                {
                    "key": key,
                    "family": step["family"],
                    "operation": step["operation"],
                    "mode": step["mode"],
                    "count": step["parameters"].get("count", 1),
                }
                for key, step in resolved["steps"].items()
            ],
            "execution_digest": fingerprint(snapshot),
            "inputs": "Protected inputs are excluded; export the profile separately for authoring.",
        }
