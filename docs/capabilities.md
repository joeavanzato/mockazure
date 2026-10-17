# MockAzure capability matrix

Current version: 0.1.0.dev0. API capabilities are available at authenticated
`GET /_mock/capabilities`. All routes use the configured local HTTPS origin.

| Operation | Status / semantics |
| --- | --- |
| GET /_mock/health | Public 200 process liveness |
| GET /_mock/ready | Public 200 readiness; sanitized 503 for unavailable DB/schema |
| GET /_mock/capabilities | Authenticated milestone and operation list |
| POST /_mock/tenants | 201; validated metadata, unique slug/domain |
| GET /_mock/tenants | UUID cursor after, limit 1â€“500 |
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

M2 adds the identity and connection contracts below. M3A adds the directory subset below; M3B adds evidence and M3C expands security
administration. M4 adds Exchange/UAL evidence; M5 expands ARM. No general OData
or large-data throughput SLA is claimed.

## Milestone 2 additions

M2 introduced the following connection surfaces. The foundation table above describes
M1; provider 501 applies only to operations outside this explicit M2 list:

| Surface | Implemented contract |
| --- | --- |
| Entra/OAuth | Local discovery/JWKS; app credentials/certificate assertions; authorization-code/S256; device; refresh |
| Graph v1.0 and beta | organization and delegated me; fixed-field select; token audiences and operation permissions |
| Exchange | 3.11.0 EXOModuleFile bootstrap and parameterless Get-OrganizationConfig through local InvokeCommand |
| ARM | subscriptions list with api-version 2021-01-01; persisted inventory; explicit Reader assignment filtering |
| Management | Users/apps, secret/certificate credentials, grants, directory roles, sessions/revocation, signing rotation, subscription provisioning |

M3B adds auditLogs below. M3C adds the bounded Conditional Access profile below.
Mailbox evidence, Activity Logs, Defender and broader Purview remain later scope.
Exchange app access requires both Exchange.ManageAsApp and a tenant-owned
Global Reader or Global Administrator service-principal role. Both module download
and command invocation enforce the role. Full Exchange RBAC scopes remain future work.
The subscription Reader model is intentionally bounded; no arbitrary ARM policy
or resource-action engine is implied. See compatibility.md and identity.md.

## Milestone 3A additions

At M3A, the authenticated capability response reported milestone 3, delivery A.
Both v1.0 and beta provide users and static security groups, membership/owner reads,
member add/remove, direct/transitive relationship traversal, typed bounded queries,
snapshot pagination and user/group delta. Writes enforce API permissions and
supported delegated administrative roles/ownership. Unsupported fields and group
types fail explicitly. Delta supports both function-name and SDK function-call paths.

Read-only JSON batches contain up to 20 independent requests. PostgreSQL quotas
default to 600 requests per tenant/client/resource per minute; 429 includes Retry-After.
Administrative controls provide one-shot read faults, safe public dataset import,
streaming export and history cleanup. Public datasets contain no credentials,
grants, roles or sessions and are distinct from metadata-only tenant export jobs.

The [exact contract](../integrations/contracts/graph-directory.md) defines query,
permission, deletion, dataset and local-profile limits. Evidence outbox records are
durable but unprojected until M3B. This milestone does not claim directoryAudits
or complete Hawk/MES collection workflows.


## Milestone 3B additions

M3B introduced delivery B; current discovery reports milestone4, delivery mail. The evidence contract is normative
for operation permissions, P1/P2 gates, delegated roles, query operators and caps.

| Surface | Implemented behavior |
| --- | --- |
| Graph signIns | v1.0 interactive user events; beta interactive/noninteractive/workload events; list/item, declared filters/order and stable continuations |
| Graph directoryAudits | Native directory/licensing/risk facts and explicitly imported history; actor/target filters |
| Graph Identity Protection | riskDetections/riskyUsers reads, permission-checked confirmCompromised/dismiss and immutable history |
| Graph licensing | subscribedSkus, delegated licenseDetails, usageLocation and assignLicense; transactional seat accounting |
| Host-local managed identity | App Service token protocol, system/user identities and host credentials; shared Graph authorization |
| Evidence management | Import/export, clock/delay/retention, drain, fenced job retry, generation reset and bounded cleanup |
| Fault injection | Persisted next_read_429 and next_page_429 with Retry-After |

Payloads are checked against the bundled pinned Microsoft schemas. Historical
data is declared input, not an inference that an attack actually happened.
Risk levels are declared simulation values; no proprietary risk score is computed.
Final client qualification status is recorded in evidence-execution.md.

## Milestone 3C additions

M3C introduced milestone3, delivery C; those operations remain supported. Microsoft Graph v1.0 metadata
and operation-specific permissions govern the profile; beta shares only declared
compatible operations.

| Surface | Implemented behavior |
| --- | --- |
| Applications/service principals | Distinct object/app IDs; CRUD, owners, credential metadata, add/remove password; certificate metadata/validation profile |
| Devices | Directory inventory and declared delegated write profile; app-only device writes rejected |
| Permission grants | OAuth delegated grants and application role assignments tied to canonical permission IDs; new token issuance reflects changes |
| Directory roles | Tenant-wide built-in activation/membership and unified role assignments; current roles enforce delegated operations |
| Authentication methods | Concrete descriptor inspection with permission-based disclosure; authenticated management enrollment |
| Registration report | Current persisted enrollment/role inventory; P1/P2 and report-reader enforcement; bounded filters and stable pagination |
| User signInActivity | Selected last interactive/noninteractive attempts and successful sign-in; retained summary independent of evidence cleanup |
| Conditional Access | Policy/location CRUD; user/group/resource/client-category targets, exclusions, IPv4/v6 CIDRs, block/MFA/report-only evaluation |
| Security evidence | Transactional immutable mutation/sign-in facts, including permission-controlled policy decisions |

Existing access-token permission claims retain their documented lifetime. Credential
and grant edits affect subsequent authentication/refresh; current identity and
directory-role state remains enforced. Policies are reevaluated for pending codes,
device approvals and refresh. Physical second-factor, passwordless and SSPR services
are outside this descriptor profile. Unsupported enforcement conditions fail
explicitly. Custom roles/PIM, device compliance and workload identity CA are not
implied by these families.

See [exact security contracts](security-contracts.md), [client qualification
contract](../integrations/contracts/graph-security.md), [measured results](security-verification.json)
and [execution decisions](security-decisions.md). Final review status is in
[security execution](security-execution.md).

## Milestone 4 additions

Capability discovery reports milestone4, delivery mail.

| Surface | Implemented behavior |
| --- | --- |
| Graph inbox rules | v1.0/beta bounded v1 contract; user/UPN/me list/item/create/update/delete; current permissions/ownership; hidden and read-only rules; immutable page versions |
| Exchange mailbox configuration | Canonical recipient identities, forwarding precedence and persisted primary/archive/folder/CAS state; current role/command/recipient scopes |
| Original Exchange commands | Fixed whitelisted HTTP proxies plus original compiled Get-EXOMailbox/statistics/folders/CAS routes; no replacement compiled cmdlets |
| Inbox/sweep rules | Distinct persisted rule families, lifecycle and ordering; actual IncludeHidden command coverage |
| Message trace/detail | Immutable imported rows, documented bounded date/filter profile, 5000-row continuation and shared 100-request/300-second throttling |
| Graph UAL queries | Explicit AuditLogsQuery permissions, workload unions, Purview roles and Audit entitlement; durable create/list/get/status/records |
| Audit evidence | Native mailbox mutations and declared historical input; immutable fenced projection; SQL materialized retained query results independent of source cleanup/reset |

Rules cap at1000 per mailbox and100 per response page. Queries retain results for
seven days; continuation handles last one hour. Unsupported query combinations
fail explicitly. Expired UAL result rows and obsolete rule versions support bounded local maintenance;
active snapshots remain pinned and expired query/job tombstones are retained.
No message delivery, full Exchange policy engine, eDiscovery or broader Purview
service is implied. See [mail contracts](mail-contracts.md) and
[measured qualification](mail-verification.json).


## Milestone 5 additions

Capability discovery reports milestone5, delivery arm.

| Surface | Implemented behavior |
| --- | --- |
| ARM inventory | Owned subscriptions, resource groups and declared resources; SQL scope filtering, bounded filters/expand and opaque pagination |
| Group/tag mutations | Permission-checked create/update/delete, tag replace/merge/delete; immutable facts and serialized state |
| Durable deletion | PostgreSQL worker job, lease fencing, restart-safe poll state and atomic terminal evidence |
| ARM authorization | Current app/user/transitive-security-group assignments; Reader, Contributor, Owner, User Access Administrator; Actions minus NotActions |
| Public RBAC | Scoped definitions and assignment CRUD/list; canonical principal identity, inherited grants and current authorization |
| Activity Logs | Actual EventData fields, exact100ns times, SQL filter/select, snapshot paging, retention/reset/revocation boundaries |
| Retry qualification | Admin-only scoped one-shot429/503 faults with Retry-After; fault state persists across replicas |

Discovery supports2020-01-01/2021-01-01; resource groups/inventory2021-04-01;
tags also2024-07-01; RBAC2022-04-01, plus original-Az role definitions2022-05-01-preview
and assignments2020-08-01-preview; Activity Logs2015-04-01. Only explicitly listed
operations gain compatibility versions. No arbitrary deployment, custom role writes,
PIM, management-group hierarchy, resource-provider execution or whole-Azure fidelity
is implied. Original collectors retain their own output behavior.


## M6 bounded maintenance and measured scale

Authenticated POST /_mock/tenants/{id}/maintenance/cleanup and the matching
CLI reclaim expired UAL result items, obsolete mailbox rule history and expired
directory continuations within a shared deletion budget (default500, maximum1000).
Live continuation pins and query/job tombstones are preserved. Audit and mailbox
phases use separate atomic transactions to avoid lock-order inversion; a later
phase failure is safely retried without rolling back an earlier committed phase.
See [maintenance](maintenance.md).

Small and medium current-source scale profiles passed exact concurrent paging
across API/worker recovery. The100000-user/1000000-record profile failed during
import at979000 acknowledged records after a PostgreSQL WAL allocation error.
It is not qualified at the declared large target. Full measurements and limits
are in [release-scale.md](release-scale.md); release readiness is governed by
[release-execution.md](release-execution.md).
