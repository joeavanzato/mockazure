# Query simulation contracts

Resource Graph currently implements `POST /providers/Microsoft.ResourceGraph/resources?api-version=2024-04-01` over canonical PostgreSQL ARM inventory. Requests and responses are validated against the pinned official Swagger. It uses the ARM audience and current scoped roles. Unauthorized resources, groups, and subscriptions are removed from both input tables before joins or aggregation; revocation is checked on each request. No separate shadow inventory is created.

The current tables are Resources and ResourceContainers (subscriptions and resource groups). Management groups, other Resource Graph tables, facets, partial-scope execution, and authorizationScopeFilter are rejected explicitly. Object-array and typed-table output formats work. `$top` defaults to 1,000; `$skip` is supported and resultTruncated indicates additional rows. Opaque `$skipToken` continuation is durable across API replicas and bound to the query, options, scope, principal, permissions and cloud generation. Tokens expire after one hour; query-result changes invalidate them explicitly. Each page rechecks current access and uses the original evaluation time. Identical page retries return identical results. Log Analytics and diagnostics are described separately below.

The typed parser/evaluator supports where, project, project-away, project-rename, extend, distinct, order/sort, take/limit, top, count, summarize, union, equality joins, and mv-expand. Resource Graph has a default Resources table, at most three combined join/union operations, at most three expansions, expansion default128/maximum2,000, no let or leftanti join, and no repeated right-hand table. The separate Log Analytics language profile permits scalar/table let and leftanti; this does not yet expose its HTTP service.

Scalar support includes explicit string/int/long/real/datetime/timespan/bool casts, parse_json, null/empty checks, coalesce, iff/case, concatenation/substrings/case conversion, equality/in/string term and substring operators with case variants, now/ago/bin and typed arithmetic. Aggregates are count/countif/sum/avg/min/max/dcount/arg_max. Null string inputs become empty strings; nullable boolean and numeric expressions retain KQL semantics. Union type conflicts produce distinct typed columns and preserve name collisions. dcount is deterministic exact distinct within the local bounds rather than production approximate estimation.

This is an explicit language subset. No arbitrary plugins, SQL, Python evaluation, external tables, regex extraction, user-defined functions, query parameters, join strategy hints, fuzzy union, union withsource, project-away wildcards, or composite arg_max return aliases are accepted. Use explicit column names and aliases. Unsupported syntax, unknown columns/functions even on empty input, and budget exhaustion return structured errors rather than empty successes.

The shared ARM schema validator also bounds serialized request/response objects to 2 MiB; the query route bounds incoming requests more tightly to 256 KiB. Oversized responses fail explicitly rather than truncate their cells.

Emulator limits: 65,536 query UTF-8 bytes, 10,000 tokens, 64 nested parser frames, 1,000 explicit subscriptions (10,000 authorized implicit subscriptions), 30 seconds of evaluator time, 100,000 returned rows, 64 MiB accounted table values, and 1,000,000 intermediate/expanded rows. These are local limits, not Microsoft service guarantees. Database loading and full concurrent-process resource bounds remain the F4/G stress gates. Input/table accounting is conservative JSON-plus-container overhead, not an RSS measurement.

The [operation catalog](../integrations/contracts/cloud/queries.json) bundles 126 hash-verified official source files, including schema dependencies and upstream licenses. Verification is offline: `python tools/acquire_cloud_contracts.py --service queries --verify-only`. Unit and PostgreSQL integration tests exercise semantics, current authorization, hidden right-side rows, invalid queries, and budget errors. Four-client F qualification is pending; no throughput or full-language compatibility is claimed.


## Log Analytics local profile (F2 implementation)

The dedicated `logs` origin defaults to `https://localhost:8449`; its resource
audience is `https://api.loganalytics.io` (the public `.azure.com` resource alias is
also recognized). Application tokens use current Azure workspace RBAC, with no
invented application `Data.Read` role. Delegated tokens additionally require
`Data.Read`. Both query inputs in a join and every additional workspace are
authorized before records are loaded; an inaccessible workspace fails the request.

GET/POST `/v1/workspaces/{workspaceId}/query`, GET metadata and legacy POST
`/v1/$batch` return the pinned Microsoft v1 envelopes. Body and URL timespans
intersect before evaluation. `Prefer: wait=1..600` is accepted with the local
execution ceiling of 30 seconds. Other preferences are explicitly rejected;
statistics/render output is not implemented. Batch accepts 1–20 POST `/query`
entries and retains individual errors. Cross-workspace body/URL lists work;
`workspace()` expressions and resource-context query paths are not implemented.

The sourced tables are AzureActivity, AzureDiagnostics, StorageBlobLogs,
StorageFileLogs, StorageQueueLogs, StorageTableLogs and AZKVAuditLogs. Their static
column definitions are in `src/mockazure/monitor/data/tables.json`, with immutable
source references. AzureDiagnostics exposes the documented baseline columns and
AdditionalFields; automatic creation of new columns is not implemented.

Administrative POST `/_mock/tenants/{tenant}/cloud/workspaces/{workspace}/logs/{table}`
accepts `rows` and equally sized distinct `source_ids` (10,000 rows / 8 MiB per
batch, 1 MiB per row). Imports validate types, owned parents, workspace TenantId
and table Type. Replaying identical source identities is idempotent; conflicting
content rolls the complete batch back. Imported rows are distinct from native
service events and cannot authorize diagnostic publication. Cloud export/import,
reset and bounded workspace-retention cleanup include logs; restored logs are
imports. Native publication is described below; original SDK qualification and
concurrent evaluator budget verification remain F4/G. Local passing tests are
not evidence that those later gates passed.


## Diagnostic settings and native delivery (F3 implementation)

Microsoft.Insights discovery exposes diagnosticSettings and category resources.
Settings use 2021-05-01-preview, with the separate subscription schema where
applicable. Categories support both pinned 2017-05-01-preview and consolidated
2021-05-01-preview contracts; categoryGroups is omitted from the older response.
Storage settings attach to the four `{service}Services/default` children, and
Key Vault settings attach to vaults. Subscription settings accept published
Activity categories; only Administrative events currently have native producers.

Saving a setting checks current source diagnostic rights and destination read
plus workspace sharedKeys/action or Storage listKeys/action rights. Local
delivery receives a scoped internal capability, never a retained bearer token
or account key. Source events atomically capture enabled categories, destination
identity and table mode. Later category/mode changes affect new events; removing
a destination or deleting/recreating its resource invalidates pending work.
Storage firewall denial without the AzureServices bypass also invalidates it.
Storage child scopes inherit account/resource-group/subscription RBAC; creation
of a new role assignment directly on a virtual child-service resource is not
yet supported.

Each destination has a separate durable, lease-fenced job. Transient local sink
errors retry after 5/10 seconds, with a maximum of three claims. Successful
Blob/Files/Queues/Tables operations and public reads map to StorageWrite,
StorageDelete or StorageRead, Key Vault secret operations to AuditEvent, and
native ARM facts to Administrative activity. Failed/denied data-plane operations
are not yet diagnostic producers. Namespace ACL reads and snapshot-inclusive
share listings currently omit read events; some Files property/metadata/range
reads share a generic operation name. Internal archive writes never create another
source event. Only supported typed columns are projected; secret/key values and
reusable authentication material are not copied into log records.

Blob archives use `insights-logs-{category}` (or `insights-activity-logs`) and
`resourceId=/SUBSCRIPTIONS/.../y=YYYY/m=MM/d=DD/h=HH/m=00/PT1H.json`, with a JSON
`records` envelope and a 64 MiB local hourly-archive limit. Destination writes
and job completion commit together; public correlation IDs do not grant
idempotency authority. Workspace delivery selects the sourced dedicated table
or the bounded AzureDiagnostics baseline. No Event Hub, marketplace, metrics,
dynamic column creation or diagnostic-setting retention policy is emulated.

Cloud snapshots include settings and captured delivery state. Authenticated
administrative restore validates owned source/destination relationships and
explicitly rebinds captured pending deliveries to restored local resources,
preserving source IDs and advancing claim epochs. It does not republish imported
service facts. Reset removes diagnostic jobs/settings. Completed job audit has
30-day bounded cleanup; active work is preserved. Generic job status exposes
delivery failures/results. Native four-client recovery and load qualification
remain F4/G requirements.
