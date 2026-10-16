from uuid import UUID

from fastapi import APIRouter, Request, Response

from mockazure.admin.errors import AdminError
from mockazure.jobs.exports import import_tenant
from mockazure.jobs.repository import TenantNotFound, enqueue_export, get_export_result, get_job
from mockazure.jobs.schemas import ExportSnapshot, ImportRequest, JobView
from mockazure.tenants.schemas import TenantView

router = APIRouter(prefix="/_mock/tenants")


@router.post("/{tenant_id}/exports", status_code=202, response_model=JobView)
async def export(tenant_id: UUID, request: Request, response: Response):
    try:
        async with request.app.state.database.sessions.begin() as session:
            job = await enqueue_export(session, tenant_id)
    except TenantNotFound:
        raise AdminError(404, "TenantNotFound", "Tenant not found") from None
    base = str(request.app.state.settings.admin_base_url).rstrip("/")
    response.headers["Location"] = f"{base}/_mock/tenants/{tenant_id}/jobs/{job.id}"
    return job


@router.get("/{tenant_id}/jobs/{job_id}", response_model=JobView)
async def job_get(tenant_id: UUID, job_id: UUID, request: Request):
    async with request.app.state.database.sessions() as session:
        job = await get_job(session, tenant_id, job_id)
    if job is None:
        raise AdminError(404, "JobNotFound", "Job not found")
    return job


@router.get("/{tenant_id}/jobs/{job_id}/result", response_model=ExportSnapshot)
async def result_get(tenant_id: UUID, job_id: UUID, request: Request):
    async with request.app.state.database.sessions() as session:
        job = await get_job(session, tenant_id, job_id)
        if job is None:
            raise AdminError(404, "JobNotFound", "Job not found")
        result = await get_export_result(session, tenant_id, job_id)
    if result is None:
        raise AdminError(409, "JobNotReady", "Job has no successful result")
    return result


@router.post("/import", status_code=201, response_model=TenantView)
async def tenant_import(data: ImportRequest, request: Request, response: Response):
    async with request.app.state.database.sessions.begin() as session:
        tenant = await import_tenant(session, data.snapshot, replacement=data.replacement)
    base = str(request.app.state.settings.admin_base_url).rstrip("/")
    response.headers["Location"] = f"{base}/_mock/tenants/{tenant.id}"
    return tenant
