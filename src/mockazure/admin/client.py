import ssl
from pathlib import Path
from uuid import UUID

import httpx

from mockazure.jobs.schemas import ExportSnapshot, JobView
from mockazure.tenants.schemas import TenantCreate, TenantPage, TenantView


class AdminClientError(Exception):
    pass


class AdminClient:
    def __init__(self, base_url: str, *, ca_file: Path, token_file: Path):
        if httpx.URL(base_url).scheme != "https":
            raise ValueError("Management client requires HTTPS")
        context = ssl.create_default_context(cafile=str(ca_file))
        self.http = httpx.AsyncClient(
            base_url=base_url,
            verify=context,
            trust_env=False,
            follow_redirects=False,
            timeout=10,
            headers={"Authorization": "Bearer " + token_file.read_text().strip()},
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.http.aclose()

    async def request(self, method, path, **kwargs):
        try:
            response = await self.http.request(method, path, **kwargs)
            if not 200 <= response.status_code < 300:
                raise AdminClientError(f"Management request failed (HTTP {response.status_code})")
            return response.json()
        except (httpx.HTTPError, ValueError):
            raise AdminClientError(
                "Management connection failed; check endpoint, CA and service"
            ) from None

    async def create_tenant(self, data: TenantCreate) -> TenantView:
        return TenantView.model_validate(
            await self.request("POST", "/_mock/tenants", json=data.model_dump(mode="json"))
        )

    async def get_tenant(self, tenant_id: UUID) -> TenantView:
        return TenantView.model_validate(await self.request("GET", f"/_mock/tenants/{tenant_id}"))

    async def list_tenants(self, after: UUID | None = None, limit: int = 100) -> TenantPage:
        params = {"limit": limit}
        if after is not None:
            params["after"] = str(after)
        return TenantPage.model_validate(await self.request("GET", "/_mock/tenants", params=params))

    async def enqueue_export(self, tenant_id: UUID) -> JobView:
        return JobView.model_validate(
            await self.request("POST", f"/_mock/tenants/{tenant_id}/exports")
        )

    async def get_job(self, tenant_id: UUID, job_id: UUID) -> JobView:
        return JobView.model_validate(
            await self.request("GET", f"/_mock/tenants/{tenant_id}/jobs/{job_id}")
        )

    async def get_export_result(self, tenant_id: UUID, job_id: UUID) -> ExportSnapshot:
        return ExportSnapshot.model_validate(
            await self.request("GET", f"/_mock/tenants/{tenant_id}/jobs/{job_id}/result")
        )

    async def import_tenant(
        self, snapshot: ExportSnapshot, replacement: TenantCreate | None = None
    ) -> TenantView:
        return TenantView.model_validate(
            await self.request(
                "POST",
                "/_mock/tenants/import",
                json={
                    "snapshot": snapshot.model_dump(mode="json"),
                    "replacement": replacement.model_dump(mode="json") if replacement else None,
                },
            )
        )

    async def upsert_sku(self, tenant_id: UUID, value):
        from mockazure.licensing.schemas import SkuView

        return SkuView.model_validate(
            await self.request(
                "PUT",
                f"/_mock/tenants/{tenant_id}/licensing/skus/{value.sku_id}",
                json=value.model_dump(mode="json", exclude={"sku_id"}),
            )
        )
