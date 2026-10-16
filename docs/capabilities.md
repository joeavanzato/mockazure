# MockAzure capability matrix

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
| Unsupported provider operations | 501 MockAzureOperationNotImplemented |

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

M2 adds the identity and connection contracts below. M3 expands stateful Graph
operations and policies; M4 adds Exchange/UAL evidence; M5 expands ARM. No general
OData, delta or large-data throughput qualification is claimed yet.

## Milestone 2 additions

The current source now reports milestone 2. The foundation table above describes
M1; provider 501 applies only to operations outside this explicit M2 list:

| Surface | Implemented contract |
| --- | --- |
| Entra/OAuth | Local discovery/JWKS; app credentials/certificate assertions; authorization-code/S256; device; refresh |
| Graph v1.0 and beta | organization and delegated me; fixed-field select; token audiences and operation permissions |
| Exchange | 3.11.0 EXOModuleFile bootstrap and parameterless Get-OrganizationConfig through local InvokeCommand |
| ARM | subscriptions list with api-version 2021-01-01; persisted inventory; explicit Reader assignment filtering |
| Management | Users/apps, secret/certificate credentials, grants, directory roles, sessions/revocation, signing rotation, subscription provisioning |

No users collection, auditLogs, generic OData/expand/delta, mailbox evidence,
Activity Logs, Defender, Purview or conditional-access engine is advertised yet.
Exchange app access currently uses the local Exchange.ManageAsApp grant; full
Exchange RBAC scopes and service-principal role modelling remain future work.
The subscription Reader model is intentionally bounded; no arbitrary ARM policy
or resource-action engine is implied. See compatibility.md and identity.md.
