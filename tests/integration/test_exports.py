from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import update

from mockazure.admin.app import create_app
from mockazure.db.models import Tenant
from mockazure.jobs.exports import import_tenant
from mockazure.jobs.repository import claim_job, enqueue_export, finish_export, get_export_result
from mockazure.tenants.repository import TenantConflict, create_tenant
from mockazure.tenants.schemas import TenantCreate


async def test_export_captures_enqueue_state_and_import_gets_new_id(database, tenant_payload):
    original = TenantCreate(**tenant_payload)
    async with database.sessions.begin() as session:
        tenant = await create_tenant(session, original)
        job = await enqueue_export(session, tenant.id)
        await session.execute(
            update(Tenant).where(Tenant.id == tenant.id).values(display_name="Changed")
        )
    async with database.sessions.begin() as session:
        claim = await claim_job(
            session, owner=uuid4(), now=datetime.now(timezone.utc), lease_seconds=30
        )
        assert await finish_export(session, claim, claim.payload)
    async with database.sessions.begin() as session:
        exported = await get_export_result(session, tenant.id, job.id)
        assert exported.tenant == original
        replacement = TenantCreate(slug="clone", display_name="Clone", domain="clone.test")
        imported = await import_tenant(session, exported, replacement=replacement)
        assert imported.id != exported.source_tenant_id and imported.domain == "clone.test"
        with pytest.raises(TenantConflict):
            await import_tenant(session, exported)
    async with database.sessions() as session:
        assert await get_export_result(session, uuid4(), job.id) is None


async def test_job_http_tenant_isolation_import_validation_and_pending_result(
    database, settings, tenant_payload
):
    settings.token_file.write_text("A" * 43)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings, database)),
        base_url="https://localhost:8443",
        headers={"Authorization": "Bearer " + "A" * 43},
    ) as client:
        tenant = (await client.post("/_mock/tenants", json=tenant_payload)).json()
        response = await client.post(f"/_mock/tenants/{tenant['id']}/exports")
        assert response.status_code == 202
        job = response.json()
        route = f"/_mock/tenants/{tenant['id']}/jobs/{job['id']}"
        assert (await client.get(route + "/result")).status_code == 409
        wrong = f"/_mock/tenants/{uuid4()}/jobs/{job['id']}"
        assert (await client.get(wrong)).status_code == 404
        assert (await client.get(wrong + "/result")).status_code == 404
        assert (await client.post(f"/_mock/tenants/{uuid4()}/exports")).status_code == 404
        invalid = {
            "schema_version": 1,
            "source_tenant_id": tenant["id"],
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "tenant": {**tenant_payload, "password": "do-not-import"},
        }
        assert (
            await client.post("/_mock/tenants/import", json={"snapshot": invalid})
        ).status_code == 422
        assert len((await client.get("/_mock/tenants")).json()["items"]) == 1
