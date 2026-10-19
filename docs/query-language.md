# Query language and operational limits

MockAzure implements bounded, typed subsets of KQL over current PostgreSQL
inventory and persisted log rows. It does not forward queries to Microsoft.
[API contracts](query-contracts.md) describe endpoint versions, auth, pagination,
and diagnostic-source limitations; [COVERAGE](../COVERAGE.md) records support.

| Behavior | Resource Graph | Log Analytics |
|---|---|---|
| Input tables | Resources, ResourceContainers | AzureActivity, AzureDiagnostics, StorageBlobLogs, StorageFileLogs, StorageQueueLogs, StorageTableLogs, AZKVAuditLogs |
| Scope | Current tenant and current readable subscriptions/resources | Current tenant, workspace and table RBAC on every input |
| Operators | where, project, project-away, project-rename, extend, distinct, order/sort, take/limit, top, count, summarize, union, join, mv-expand/mvexpand | Same |
| Join flavors | innerunique, inner, leftouter, fullouter | Same plus leftanti |
| let | Rejected | Scalar and table bindings |
| Operator caps | Three joins/unions combined; three mv-expand operators; no repeated right table; expansion default 128 and max 2,000 | Configured local execution/expansion caps |
| Continuation | Opaque, query/options/scope/principal-bound; one-hour expiry; source changes invalidate | No fabricated continuation; oversized results fail |
| Additional workspaces | Not applicable | Explicit body/URL workspaces; each requires current access |
| Unsupported examples | Facets, management groups, partial scopes, arbitrary tables | workspace()/resource() expressions, statistics/render, user-defined functions, full Kusto language |

Scalar functions include string/numeric/bool/datetime/timespan casts,
parse_json, isnull/isnotnull/isempty/isnotempty, coalesce, iff/iif/case,
strcat, substring, strlen, tolower/toupper, now/ago/bin, and not. Expressions
support arithmetic, comparisons, boolean logic, dynamic access, and the qualified
case-sensitive/insensitive equality, in, contains, has, startswith and endswith
forms. Aggregates are count, countif, sum, avg, min, max, dcount and arg_max.
Use explicit aliases for computed projections. dcount is deterministic exact
distinct counting within the local budget, unlike production approximation.
This is a simulation profile, not a complete Kusto implementation.

## Server configuration

These JSON settings (or their `MOCKAZURE_` environment equivalents) control
local work. They are emulator settings, not Microsoft service quotas.

| Setting | Default | Effect |
|---|---:|---|
| query_timeout_seconds | 30 | Whole scan/evaluation deadline, capped by the client's wait preference |
| query_max_rows | 100,000 | Returned rows; exceeding the cap fails |
| query_max_bytes | 67,108,864 | Conservative cumulative accounted input/intermediate values |
| query_max_expanded_rows | 1,000,000 | Input/materialization row cap and cumulative join/expansion output |
| diagnostic_delay_seconds | 0 | Initial durable delivery delay |
| diagnostic_retry_seconds | 5 | Exponential retry base, capped at one day |
| diagnostic_max_attempts | 3 | Maximum claims after retryable destination failures |

Accounting charges serialized values and structural overhead, including retained
let results, grouping and join indexes. It intentionally does not reclaim a
charge after an intermediate is freed: long pipelines can fail before reaching
a process-memory limit. It is not an RSS measurement or a global concurrency
quota. Each API replica applies its own per-query budget.

Input queries use bound SQLAlchemy predicates, tenant/workspace/resource indexes,
and a transaction-local PostgreSQL statement timeout. CPU evaluation runs off the
API event loop with a cooperative cancellation flag. An exhausted query returns
Resource Graph `QueryLimitExceeded` or Logs `QueryExecutionError`; each legacy-batch
item owns a separate transaction/connection so driver cancellation cannot poison
the remaining queries. Unknown
operators and unsafe query text fail explicitly. Raw query text is never SQL or
executable Python.

Pending diagnostic capabilities survive worker/API restart; source IDs remain
stable through an authenticated cloud snapshot restore. Restore advances lease
epochs, so an old worker cannot complete restored work. Removed/recreated
resources or revoked destinations invalidate pending work. Setting category or
table-mode changes affect future events. Cleanup preserves active jobs; opaque
Resource Graph continuations check generation and current access on every use.

## Native verification

Run `python -m tools.verify_cloud_workflows --phase F --ecosystems python powershell dotnet typescript --report work/cloud-phase-f.json`.
It uses pinned original Resource Graph, Monitor and Logs SDKs/modules, real local
TLS and tokens, real Blob writes, durable diagnostic delivery, cross-client reads
and an API/worker restart. Legacy Logs batch is a separately labeled REST check.
A configured runner or passing integration tests alone do not establish native
qualification; consult its source-matched report.
