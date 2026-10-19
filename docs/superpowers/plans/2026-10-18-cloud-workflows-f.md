# Resource Graph Log Analytics and diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution is already selected. Steps use checkboxes; implement in this session.

**Goal:** Query canonical inventory and real simulated diagnostic evidence through distinct Microsoft query APIs.
**Architecture:** Build a typed bounded query engine with separate Resource Graph and Log Analytics language profiles. Diagnostic publication consumes committed service events and writes authorized local destinations with durable retry.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker; official Python, PowerShell, .NET and JavaScript/TypeScript clients.
**Spec:** [Approved cloud workflows specification](../specs/2026-10-18-cloud-workflows-design.md).
**Program:** [Execution index and shared acceptance contract](2026-10-18-cloud-workflows.md).
**Status:** F1/F2 implemented with local verification: 125 tests pass, lint and offline references verified. F3/F4, phase review, four-client qualification and G acceptance remain pending.

## Global Constraints

- No real Microsoft tenant is contacted. VTW is untouched.
- SMB/NFS mounts are explicitly deferred.
- Graph v1.0 schema and operation alignment for every Graph operation in this program.
- Runtime validation and CI must work from cached references without contacting Microsoft.
- Preserve Apache licensing, Windows/Docker support, PostgreSQL, existing data, and previously accepted collector limitations.
- No product code is introduced by this plan. After approval, commit cohesive changes, push reviewable PRs, and do not auto-merge.
- Read the program index for mandatory contracts, migration/lifecycle rules and exact test/client commands; those requirements apply to every task.
- Paths are repository-relative. New modules are labeled Create; add empty package initializers as needed. Allocate migration numbers after the current head, retaining the descriptive suffix.
- Use existing `PrincipalContext`, `AsyncSession`, `Database`, `JobClaim`, `JobResult` and `ContentRef` types; they are not new substitutes. Transaction ownership stays with callers except durable worker entry points.

## Review Focus

- Authorization must filter both sides of a join before aggregation (F1/F2).
- KQL null/string/dynamic typing must not inherit incompatible SQL semantics (F1).
- Pagination and timespan limits must not silently omit results or accept a changed query (F2).
- Changing/removing a diagnostic destination during retry must use an explicit publication snapshot rule (F3).
- Query resource limits and partial failures must be visible in service-shaped responses (F2/F4).

## File boundaries and contracts

Define new `query/types.py`: `QueryPlan(profile: str, nodes: tuple, parameters: dict)`, `QueryResult(columns: list[tuple[str, str]], rows: list[list], truncated: bool)`, `QueryScope(tenant_id: UUID, subscription_ids: tuple[str, ...], workspace_ids: tuple[str, ...])`. Nodes are owned typed AST classes, never executable Python/SQL strings. Time is an explicit input.

Pin [Resource Graph language restrictions](https://learn.microsoft.com/en-us/azure/governance/resource-graph/concepts/query-language), [Logs Query v1](https://learn.microsoft.com/en-us/rest/api/logsquery/query/execute?view=rest-logsquery-v1), and [response format](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/response-format). Logs batch remains a separately labeled legacy compatibility operation: Microsoft documents its [March 31, 2028 cutoff](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/migrate-batch-and-beta). Prefer v1 single-query workflows; do not add retired beta endpoints.

Local evaluator defaults: 30-second execution budget, 100,000 returned rows, 64 MiB accounted intermediate values and 1,000,000 expanded rows; configurable and explicitly labeled emulator limits. Apply stricter sourced service limits where relevant. Resource exhaustion raises a defined error; truncation is used only where the API contract provides a result indicator.

## Task F1: Typed query evaluation and working Resource Graph reads

**Files**

- Create: `src/mockazure/query/types.py`, `src/mockazure/query/ast.py`, `src/mockazure/query/parser.py`, `src/mockazure/query/compiler.py`, `src/mockazure/query/functions.py`, `src/mockazure/query/profiles.py`, `src/mockazure/providers/resourcegraph/routes.py`, `integrations/contracts/cloud/queries.json`, `tests/unit/test_cloud_kql.py`, `tests/integration/test_resource_graph_queries.py`.
- Modify: `src/mockazure/admin/app.py`, `src/mockazure/providers/arm/provider_registry.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `parse_query(text: str, profile: str) -> QueryPlan`; `async execute_query(session: AsyncSession, actor: PrincipalContext, scope: QueryScope, plan: QueryPlan, now: datetime) -> QueryResult`. The caller supplies a server-derived authorized scope; compiler also applies per-table access predicates. Profiles are `resourcegraph` and `loganalytics`.

- [x] **Step 1 — Add the named failing scenarios.** test_query_typed_semantics compares fixed truth tables for null/dynamic/string casts, case-sensitive/insensitive operators, joins and aggregation. test_resourcegraph_scope_and_projection queries Resources joined to ResourceContainers with allowed subscriptions and confirms excluded subscriptions cannot affect counts. test_query_rejects_unknown rejects unknown operators/functions, undeclared table access and malicious SQL text.

```python
assert actual_rows == expected_rows
assert count_with_hidden_subscription == authorized_count
assert injected_sql_executed is False
assert unsupported.status_code == 400
```

- [x] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/unit/test_cloud_kql.py tests/integration/test_resource_graph_queries.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [x] **Step 3 — Implement the declared interfaces.** Implement where/project/project-away/project-rename/extend/distinct/order/sort/take/top/summarize/union/joins/mv-expand and let only in profiles whose official contract allows them. Functions: tostring/toint/tolong/todouble/todatetime/timespan casts, parse_json, isnull/isnotnull/isempty/coalesce, iff/case, strcat/substring/strlen/tolower/toupper, contains/has/startswith/endswith/equality/in case variants, now/ago/bin/datetime arithmetic and count/countif/sum/avg/min/max/dcount/arg_max. Pin exact null/output/type rules in fixtures. dcount uses deterministic exact distinct within the bounded profile and documents this difference from approximate production execution. Add scalar/table symbol binding and type-safe parameterized evaluation, not eval. Ship actual Resource Graph query endpoint in this task.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [x] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: query ARM inventory with a bounded KQL engine"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task F2: Workspace queries, metadata and response protocols

**Files**

- Create: `src/mockazure/monitor/log_models.py`, `src/mockazure/monitor/logs.py`, `src/mockazure/monitor/tables.py`, `src/mockazure/providers/loganalytics/routes.py`, `src/mockazure/providers/loganalytics/errors.py`, `tests/integration/test_log_analytics_queries.py`.
- Modify: `src/mockazure/query/profiles.py`, `src/mockazure/providers/resourcegraph/routes.py`, `src/mockazure/endpoints.py`, `src/mockazure/admin/app.py`, `src/mockazure/config.py`, `src/mockazure/cloud/lifecycle.py`, `src/mockazure/oauth/scopes.py`, `src/mockazure/authorization/catalog.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async ingest_log_rows(session: AsyncSession, tenant_id: UUID, workspace_id: UUID, table: str, rows: list[dict], source_ids: list[str]) -> int`; `async query_workspace(session: AsyncSession, actor: PrincipalContext, workspace_id: UUID, body: dict, now: datetime) -> dict`. Table catalog validates both ingestion and query column types, with supported metadata discovery.

- [x] **Step 1 — Add the named failing scenarios.** test_logs_tables_and_timespan ingests typed records, queries joins/aggregations/dynamic fields with intersecting timespans and checks columns/rows ordering. test_logs_cross_workspace_auth and test_logs_batch_mixed_errors isolate authorized scopes and individual batch failures. test_resourcegraph_paging binds token to query/scope/options and invalidates or snapshots explicitly according to the qualified profile.

```python
assert response['tables'][0]['columns'] == expected_columns
assert response['tables'][0]['rows'] == expected_rows
assert response_ids == requested_ids
assert denied_workspace_rows == []
```

- [x] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_log_analytics_queries.py tests/integration/test_resource_graph_queries.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [x] **Step 3 — Implement the declared interfaces.** Implement GET/POST v1 workspace query, metadata and labeled legacy v1/$batch with published envelopes, errors, timespan/preferences and per-request auth. Add dedicated service audience/origin and accurate auth challenge behavior. Resource Graph uses ARM audience, its own request/response shape and supported joins (including fullouter; no Log Analytics-only leftanti). Enforce sourced join/union budgets. Add explicit administrative typed log imports distinct from native event generation and lifecycle handling.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [x] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add typed Log Analytics workspace queries"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task F3: Diagnostic settings and real local event delivery

**Files**

- Create: `src/mockazure/monitor/diagnostics.py`, `src/mockazure/monitor/diagnostic_models.py`, `src/mockazure/monitor/publication.py`, `src/mockazure/providers/arm/diagnostic_routes.py`, `tests/integration/test_diagnostic_delivery.py`.
- Modify: `src/mockazure/providers/arm/provider_registry.py`, `src/mockazure/cloud/events.py`, `src/mockazure/jobs/dispatch.py`, `src/mockazure/cloud/lifecycle.py`, `src/mockazure/admin/app.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async mutate_diagnostic_setting(session: AsyncSession, actor: PrincipalContext, resource_id: str, name: str, operation: str, body: dict, now: datetime) -> MutationResult`; `async process_diagnostic_delivery(database: Database, claim: JobClaim, wall_now: datetime) -> JobResult`. Consume ServiceEvent, C2 Blob write internals and F2 typed ingestion. Delivery uses an internal destination capability scoped to the validated setting, not a reusable user bearer token.

- [ ] **Step 1 — Add the named failing scenarios.** test_storage_keyvault_diagnostics selects categories at correct resource/service scope, writes data/secret operations and reads published logs in both Blob and workspace destinations. test_diagnostic_retry_and_changes fails delivery, restarts, changes a setting and verifies the declared snapshot/current-eligibility rule. test_diagnostic_no_feedback prevents delivery's own storage writes recursively generating diagnostic delivery.

```python
assert observed_source_ids == expected_eligible_ids
assert retained_delivery_source_id == original_source_id
assert unsupported_category.status_code == expected_category_error
assert secret_or_key_values_in_logs == []
assert recursive_delivery_count == 0
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_diagnostic_delivery.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Pin category and settings CRUD/discovery contracts including Storage child-service scopes and subscription Activity Log destinations. Validate supported destination existence/type and configuration rights; snapshot category/destination selection when an eligible source event is committed. On delivery recheck destination existence and current trusted delivery capability; changed settings affect new events, explicit destination removal disables pending delivery. Persist outcome/failure for inspection. Map AzureActivity, StorageBlobLogs/StorageFileLogs/StorageQueueLogs/StorageTableLogs and Key Vault supported resource-specific/legacy tables to actual column references. Do not claim Event Hub or metrics delivery. Preserve public storage record format, category paths and dedup/provenance internally without inventing Microsoft columns.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: route diagnostic events to local storage and workspaces"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task F4: Query and diagnostic recovery/scale qualification

**Files**

- Create: `tests/integration/test_query_diagnostic_boundaries.py`, `integrations/python/cloud_queries.py`, `integrations/powershell/Test-CloudQueries.ps1`, `docs/query-language.md`.
- Modify: `src/mockazure/query/compiler.py`, `src/mockazure/monitor/publication.py`, `src/mockazure/cloud/lifecycle.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Keep F1/F2 signatures; expose server settings for the documented evaluator budgets and durable delivery delay/retry controls. Extend the shared runner with `queries` and `diagnostics` workflows and exact language-profile reports.

- [ ] **Step 1 — Add the named failing scenarios.** test_query_budget_and_isolation exceeds expansion/intermediate/time limits with an authorized query and verifies explicit failure. test_diagnostic_restart_export round-trips settings/logs/pending events and proves source identities survive a worker restart. test_cross_tenant_join never admits rows from a different tenant even when names collide.

```python
assert exhausted.status_code == expected_query_limit_status
assert failure_was_reported is True
assert cross_tenant_rows == []
assert delivered_source_ids_after_restart == expected_source_ids
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_query_diagnostic_boundaries.py tests/unit/test_cloud_kql.py tests/integration/test_diagnostic_delivery.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Add indexed table/resource scans, cancelable parameterized execution and accounting for dynamic expansions/joins; document supported functions/operators, limits and dcount deviation. Qualify representative SDK queries across four ecosystems and REST legacy batch separately. Verify retention/reset preserves active query snapshots as declared; coverage separately records settings CRUD, event categories, destination delivery and query availability.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "test: qualify query language and diagnostic recovery"`. Preserve unrelated work. Push/review according to the program's phase PR rules.
