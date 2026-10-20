# Graph Hunting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution remains selected. Complete D1–D4; D1's offline schema acquisition may start after A1.

**Goal:** Provide Graph v1.0 advanced hunting over 20 correlated, typed local tables.

**Architecture:** Add a hunting-specific catalog, PostgreSQL records, authorization
and Graph envelope around the reusable bounded KQL parser/evaluator. Produce records
from typed fixtures or explicitly supported native mappings; no sensor/detection engine.

**Tech Stack:** Existing Graph/OAuth/PostgreSQL/query engine, profile runner and console.

**Spec:** [Approved design](../specs/2026-10-20-living-tenant-design.md), sections 5 and 7–9.

## Global constraints

All [program constraints](2026-10-20-living-tenant.md#global-constraints) apply.
“A cloud API call alone cannot prove that a host process executed or a URL was
clicked.” “Do not implement incident updates, comments, assignment, response
actions or automatic incident-management workflows.” No arbitrary external query
inputs, full KQL claim, hidden cross-workspace access or unlabelled preview tables.

## Review focus

- Wrong column/type in an empty table must still fail validation (D1/D2).
- Permissions must constrain all join inputs before evaluation, not only output (D2).
- Synthetic host telemetry must not be reported as a locally executed process (D3).
- Graph and Logs envelopes/permissions must remain distinct despite engine reuse (D2).
- Wheel/sdist/Docker distributions must contain the same table definitions/assets (D4).

## File boundaries

`src/mockazure/hunting/` owns table contracts, records, input authorization, Graph
wire formatting and native projections. `query/` stays a service-independent bounded
evaluator with explicit profiles. Population imports/recipes and browser controls
consume the hunting interfaces; they do not execute arbitrary KQL as SQL.

## D1. Pinned table contracts, typed storage and bounded fixture imports

**Files:** Create `src/mockazure/hunting/{contracts,models,repository,schemas}.py`,
`src/mockazure/hunting/data/{tables,sources}.json`,
`src/mockazure/population/adapters/hunting.py`,
`tests/unit/test_hunting_table_contracts.py`,
`tests/integration/test_hunting_ingestion.py`.
Extend A1 contract acquisition tool and catalog. Add migration suffix `hunting_records`.

**Interfaces:** `HuntingTableContract(name, columns, availability, source_refs,
entitlements, native_mappings)`; `table_contract(name: str) -> HuntingTableContract`.
`HuntingProvenance(kind, source_ref, generator_version)` has kind
`fixture|native`; it is internal metadata.
`ingest_rows(session, tenant_id: UUID, table: str, rows: list[dict],
source_ids: list[str], provenance: HuntingProvenance) -> int`.
Management imports always construct fixture provenance on the server.
`browse_rows(session, tenant_id, table, query: ResourceQuery) -> ResourcePage`
supports catalog browsing with bounded continuations and redacted provenance.

**Exact table set:**

| Group | Names |
| --- | --- |
| Identity (4) | IdentityInfo, IdentityLogonEvents, IdentityDirectoryEvents, IdentityQueryEvents |
| Email (5) | EmailEvents, EmailAttachmentInfo, EmailUrlInfo, EmailPostDeliveryEvents, UrlClickEvents |
| Devices (8) | DeviceInfo, DeviceNetworkInfo, DeviceLogonEvents, DeviceProcessEvents, DeviceNetworkEvents, DeviceFileEvents, DeviceRegistryEvents, DeviceEvents |
| Cloud (1) | CloudAppEvents |
| Alerts (2) | AlertInfo, AlertEvidence |

- [ ] **Tests:** `test_exact_twenty_table_catalog`,
  `test_columns_and_types_match_pinned_sources`,
  `test_unsupported_or_preview_availability_is_explicit`,
  `test_ingestion_rejects_unknown_columns_and_wrong_types`,
  `test_conflicting_source_replay_is_atomic`,
  `test_management_cannot_claim_native_provenance`,
  `test_hunting_browse_is_tenant_scoped`.
  ```python
  assert len(catalog_names) == 20
  assert set(catalog_names) == expected_twenty_names
  assert foreign_tenant_rows == []
  assert inserted_on_identical_replay == 0
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_hunting_table_contracts.py tests/integration/test_hunting_ingestion.py -q`.
- [ ] **Acquire schemas:** Fetch official table references and Graph hunting metadata
  with source hashes and licenses. Store all documented column definitions and their
  types; identify fields actually generated and nullable omissions. If a listed
  table is unavailable/preview in the pinned source, preserve its explicit status
  rather than silently dropping it or substituting an unrelated table.
  Entra-specific preview tables are not an implicit 21st table.
- [ ] **Implement storage:** Tenant/table/source uniqueness; indexed event-time and
  identity lookup where needed; typed payload validation before persistence.
  Import cap 10,000 rows/8 MiB per batch, 1 MiB per row. Retention/reset/export and
  catalog browsing participate in existing tenant lifecycle, with no secret export.
- [ ] **Verify:** New tests pass; source verifier passes offline; migration upgrades
  existing populated instances without changing their logs or alerts.
- [ ] **Commit:** `feat: add typed hunting tables and local ingestion`.

## D2. Graph hunting execution, authorization and wire contract

**Files:** Create `src/mockazure/hunting/{service,authorization,wire}.py`,
`src/mockazure/providers/graph/hunting_routes.py`,
`tests/unit/test_hunting_queries.py`,
`tests/integration/test_graph_hunting.py`,
`tests/integration/test_hunting_authorization.py`.
Modify Graph router registration, sourced permission catalog, query profiles/parser
only where required, and existing tenant entitlement models using an additive migration
if necessary. Do not piggyback authorization on ARM/Logs audience checks.

**Interfaces:** `HuntingRequest(Query: str, Timespan: str | None,
workspaceId: UUID | None)` and `HuntingResponse(schema, results)` reflect pinned
Graph metadata. `run_hunting(session, actor: PrincipalContext,
request: HuntingRequest, *, wall_now: datetime, limits: QueryLimits) ->
HuntingResponse`.
`authorize_inputs(session, actor, table_names: set[str],
workspace_id: UUID | None) -> HuntingScope` returns tenant-bound authorized sources;
`HuntingScope(tenant_id, workspace_id, tables, visibility)` never accepts caller-
supplied row predicates.

**Route:** POST `/v1.0/security/runHuntingQuery`. Require Graph audience and
ThreatHunting.Read.All plus documented delegated role/data/entitlement checks.
No beta or legacy Defender transport is implied.

- [ ] **Tests:** `test_graph_hunting_request_and_schema_envelope`,
  `test_timespan_intersects_query_filter`,
  `test_empty_input_still_validates_columns`,
  `test_join_right_input_is_authorized_before_loading`,
  `test_workspace_fallback_never_reaches_foreign_data`,
  `test_delegated_visibility_is_not_app_only_visibility`,
  `test_unknown_syntax_and_budget_exhaustion_are_explicit`,
  `test_hunting_does_not_change_logs_or_resourcegraph_contract`.
  ```python
  assert set(response) >= {"schema", "results"}
  assert "tables" not in response
  assert foreign_subject_id not in json.dumps(response)
  assert unknown_column_response.status_code == 400
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_hunting_queries.py tests/integration/test_graph_hunting.py tests/integration/test_hunting_authorization.py -q`.
- [ ] **Implement input scope:** Model explicit local table entitlements and the
  supported delegated visibility profile from pinned documentation. Broader Defender
  unified RBAC remains a declared limitation, not a bypass. Implement documented
  workspace selection/fallback only within the caller's authorized local primary
  hunting workspace; never use fallback to query the requested foreign workspace.
  Arbitrary Log Analytics tables are not made available through this API.
- [ ] **Implement query execution:** Add `hunting` as a language profile using
  `query.parser.parse_query` and `query.compiler.evaluate_query`. Load only authorized,
  time-filtered typed records under the existing accounting/deadline discipline.
  Bound defaults by existing QueryLimits (30 s, 100,000 output rows, 64 MiB accounted
  values, 1,000,000 intermediate rows), or stricter pinned service limits.
  Capture one evaluation time from the declared lab evidence clock. Security checks
  still use wall_now. Translate Graph type names and dynamic/temporal values correctly.
- [ ] **Implement language contract:** Existing where/project/extend/summarize,
  order/take, distinct, let, union, equality joins and mv-expand form the baseline.
  Include only sourced functions needed by D4's cookbook. Never silently truncate
  or fabricate nextLink; follow the sourced response/error contract for limits.
- [ ] **Verify:** New tests and existing KQL/Logs/ResourceGraph suites pass; original
  Graph PowerShell and Python SDK issue a real hunting request with schema/results.
- [ ] **Commit:** `feat: implement authorized Graph advanced hunting`.

## D3. Correlated telemetry generators and native evidence projections

**Files:** Create `src/mockazure/hunting/{projection,publication}.py`,
`src/mockazure/population/generators/{business_hunting,hunting_identity,hunting_mail,hunting_devices}.py`,
`src/mockazure/profiles/data/data/business-security-telemetry.json`,
`tests/unit/test_hunting_generation.py`,
`tests/integration/test_hunting_projection.py`,
`tests/integration/test_hunting_correlations.py`.
Modify existing source dispatch/projectors, generator registry and profile layers.

**Interfaces:** `HuntingProjection(table, source_id, row)` is a strict model.
`project_hunting(source: SourceEnvelope) -> list[HuntingProjection]` maps only
documented native source kinds. `publish_hunting(session, source, projections) ->
int` calls D1 ingestion with server-owned native provenance, preserving source
generation/version. Generators retain the existing generate protocol and emit
fixture mutations for synthetic sensor events.

- [ ] **Tests:** `test_every_table_has_nonempty_schema_valid_generation`,
  `test_identity_device_and_mail_ids_join`,
  `test_document_hash_matches_attachment_and_file_evidence`,
  `test_alert_evidence_resolves_readonly_alert_identity`,
  `test_native_reprojection_is_idempotent`,
  `test_graph_directory_read_does_not_invent_identity_sensor_query`,
  `test_mail_send_does_not_invent_url_click_or_malware_verdict`,
  `test_import_restore_does_not_republish_as_native`.
  ```python
  assert tables_with_rows == expected_twenty_names
  assert attached_sha256 == generated_document_sha256
  assert native_url_clicks_from_send == 0
  assert native_source_ids_after_retry == native_source_ids_before_retry
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_hunting_generation.py tests/integration/test_hunting_projection.py tests/integration/test_hunting_correlations.py -q`.
- [ ] **Implement fixtures:** Use A1 service mappings and A2 hashes for all 20 tables.
  Host/process/network/registry events are synthetic sensor history; no host execution.
  Keep NetworkMessageId distinct from internetMessageId, Defender device IDs distinct
  from directory/Intune IDs, and account identity mappings explicit.
  Do not equate IdentityLogonEvents/IdentityQueryEvents with every Entra/Graph call.
- [ ] **Implement native maps:** For local outcomes with a documented semantic
  mapping, publish cloud/mail/security projections via existing durable jobs.
  Snapshot inventory remains labeled as such. If the lab cannot substantiate a
  telemetry property, leave its supported nullable field unset; don't invent
  detector verdicts. B1 mapping metadata names exact native destinations.
- [ ] **Implement optional layers:** Add suspicious-consent, mailbox-forwarding,
  unusual-download, privilege-change and unusual-secret-access overlays as
  declarative data/operations, preserving ordinary business defaults.
  Export ground truth with public IDs and source provenance, without credentials.
  Existing alerts/incidents remain read-only; no incident update route is added.
- [ ] **Verify:** New tests pass; queries join identity↔device, email↔attachment,
  alert↔entity and user↔cloud data across actual populated records.
- [ ] **Commit:** `feat: correlate hunting telemetry with business activity`.

## D4. Hunting console, query cookbook and combined qualification

**Files:** Create `src/mockazure/console/static/hunting.js`,
`docs/hunting-cookbook.md`, `docs/qualification/living-tenant-results.md`,
`tests/browser/test_hunting_console.py`,
`tests/acceptance/test_living_hunting_workflows.py`,
`tests/unit/test_living_tenant_package.py`.
Modify console navigation/API modules, hunting catalog adapters, existing package
asset settings, `tools/verify_living_tenant.py`, COVERAGE.md and deployment/profile docs.

**Interfaces:** Authenticated management POST
`/_mock/tenants/{tenant}/hunting/query` accepts `identity_ref` plus the Graph request
body and forwards through the existing local transport with a named test identity.
The shared console session never substitutes for ThreatHunting.Read.All.
Catalog metadata exposes table/column/type/source availability and provenance modes,
not a fabricated public Microsoft schema endpoint.

**Cookbook:** At least one query per table plus the four D3 relationship joins;
include time filtering, count/summarize, dynamic fields, null handling and ordering.
Examples reference populated template data and expected assertions. No arbitrary
plugin, SQL, Python, external URL or unbounded query support.

- [ ] **Tests:** `test_catalog_exposes_all_twenty_tables`,
  `test_console_query_requires_named_authorized_identity`,
  `test_query_results_escape_html`,
  `test_cookbook_queries_match_expected_entities`,
  `test_installed_packages_have_identical_schema_assets`,
  `test_incident_mutation_remains_unsupported`.
  ```python
  assert len(schema_catalog) == 20
  assert cookbook_report["failed_cases"] == []
  assert wheel_asset_hashes == sdist_asset_hashes == docker_asset_hashes
  assert incident_patch_response.status_code in {404, 405, 501}
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/browser/test_hunting_console.py tests/acceptance/test_living_hunting_workflows.py tests/unit/test_living_tenant_package.py -q`.
- [ ] **Implement UI/docs:** Reuse service browser/table paging and existing console
  styles. Add a bounded query editor with schema results, reference time, permission
  errors and cookbook examples. Make import/native provenance inspectable through
  management metadata without modifying public Graph row schemas.
- [ ] **Qualify:** Add `--phase hunting|all` to A6 verifier. Run original Graph SDK/
  PowerShell hunting, existing collector workflows and the supported Exchange/Azure
  paths end-to-end on a fresh local instance. Use two tenants with overlapping
  synthetic names and unrelated access to validate isolation. No real endpoints.
- [ ] **Scale/package:** Qualify small complete tenant (20 users, all surfaces and
  all 20 hunting tables) and large preset (2,500 users; 100,000 total hunting rows;
  bounded collaboration content). Record actual counts/bytes/memory/time.
  Build wheel/sdist and Docker from the same candidate; compare source manifests,
  schema/template/corpus assets and representative results. Native and Compose
  qualification must both run; neither substitutes for the other.
- [ ] **Final verification:** Run full unit/integration regression and relevant
  browser/acceptance gates against final candidate, inspect hosted CI, and complete
  one independent whole-program review under the Native workflow. Fix findings and
  rerun affected checks. Publish exact commands/client versions/hashes/limitations.
- [ ] **Commit:** `test: qualify living tenant and Graph hunting workflows`.
