# Capability matrix: foundation

Current version: 0.1.0.dev0. API capabilities are available at authenticated
`GET /_mock/capabilities`. All routes use the configured local HTTPS origin.

| Operation | Status / semantics |
| --- | --- |
| GET /_mock/health | Public 200 process liveness |
| GET /_mock/ready | Public 200 readiness; sanitized 503 for unavailable DB/schema |
| GET /_mock/capabilities | Authenticated milestone and operation list |
| POST /_mock/tenants | 201; validated metadata, unique slug/domain |
| GET /_mock/tenants | UUID cursor after, limit 1–500 |
| GET /_mock/tenants/{id} | 200 or 404 |
| POST /_mock/tenants/{id}/exports | 202 durable metadata snapshot job |
| GET /_mock/tenants/{id}/jobs/{job_id} | Tenant-scoped job status |
| GET /_mock/tenants/{id}/jobs/{job_id}/result | 200 snapshot; 409 pending/failed; 404 wrong tenant/job |
| POST /_mock/tenants/import | 201 new tenant from snapshot and optional replacement metadata |
| Provider paths (Entra, Graph, Exchange, ARM) | 501 MockAzureOperationNotImplemented |

Except health/readiness, management routes require the local admin.token bearer
credential. Invalid tokens receive 401, invalid input 422, duplicate tenant identity
409. Unknown management routes return 404; incorrect methods return 405.
Errors have `error.code`, `error.message`, and `error.request_id`.

Import body:
```json
{
  "snapshot": {
    "schema_version": 1,
    "source_tenant_id": "00000000-0000-0000-0000-000000000001",
    "captured_at": "2026-10-16T00:00:00Z",
    "tenant": {"slug": "example", "display_name": "Example", "domain": "example.test", "licences": []}
  },
  "replacement": {"slug": "copy", "display_name": "Copy", "domain": "copy.test", "licences": []}
}
```

M2 adds Microsoft authentication/connectivity; M3 adds stateful Graph operations;
M4 Exchange/UAL; M5 ARM. Permission and policy enforcement belongs to those
provider contracts. No Hawk/Microsoft Extractor Suite compatibility, OData, delta,
Microsoft tokens or large-data throughput is claimed by the foundation.
