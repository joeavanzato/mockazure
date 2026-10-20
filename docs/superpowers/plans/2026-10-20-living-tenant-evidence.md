# Basic Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution remains selected. Complete B1–B3 after Phase A.

**Goal:** Make supported business actions and safely attributable failures produce correct, collectible local evidence.

**Architecture:** Reuse existing domain facts, transactional source records, durable
projection jobs and diagnostic delivery. Add explicit source mappings and a narrow
failed-operation capture boundary; no broker or generalized simulation engine.

**Tech Stack:** Existing Python/PostgreSQL worker and local Graph/Exchange/ARM/Logs
collection surfaces.

**Spec:** [Approved design](../specs/2026-10-20-living-tenant-design.md), section 4 and completion gates.

## Global constraints

All [program constraints](2026-10-20-living-tenant.md#global-constraints) apply.
“Project only the evidence surfaces appropriate to that action.”
“Exclude passwords, tokens, keys, reusable credential material and secret values
from evidence, manifests and browser previews.”
Use existing evidence reference-time, delay and retention mechanisms.

## Review focus

- Mutation commit and its source fact cannot disagree (B1).
- A rolled-back denied request must not lose safely attributable diagnostics (B2).
- Unknown tenant/foreign targets must not be logged as if they were owned resources (B2).
- Imported history cannot publish new native delivery or authorize access (B1/B3).
- Eventual visibility must be distinguished from missing evidence without adding
  a new failure-simulation feature (B3).

## File boundaries

`evidence/operations.py` holds declared mapping metadata;
domain `facts.py` modules own capture; `evidence/failures.py` owns sanitized
failure transactions; existing `monitor/diagnostic_*` modules own delivery.
Keep Microsoft payloads free of local provenance fields.

## B1. Versioned operation-to-evidence mapping and missing native sources

**Files:** Modify `src/mockazure/evidence/{operations,schemas,projectors,sources}.py`,
`src/mockazure/mail/facts.py`, `src/mockazure/workloads/facts.py`, and existing
directory/security/ARM source capture modules.
Create `src/mockazure/evidence/data/operation_mappings.json`,
`tests/unit/test_operation_evidence_contracts.py`,
`tests/integration/test_business_action_evidence.py`.
Update A1 operation manifest and existing monitor projection modules where a
supported operation uses an inaccurate generic operation name.

**Interfaces:** `OperationEvidenceContract(operation_key, source_kind, outcome,
destinations, schema_refs, native, limitations)` is strict. `destinations` names
existing evidence families; `mapping(operation_key: str) ->
OperationEvidenceContract` returns the declared contract. Use existing
`SourceEnvelope`, `ProjectionBatch` and domain source identity rules.
Mapping versions are persisted with source provenance when required to replay safely.

- [ ] **Tests:** `test_operation_manifest_has_explicit_evidence_disposition` requires
  every A1 operation to map to evidence or an explicit sourced no-evidence reason;
  `test_commit_and_source_are_atomic` rolls back an attempted mutation and finds
  no successful fact; `test_committed_action_projects_once` retries a native job
  and finds one event per declared destination. Compare actor/target/time/outcome.
  ```python
  assert successful_facts_after_rollback == []
  assert collected_source_ids == expected_source_ids
  assert len(collected_source_ids) == len(set(collected_source_ids))
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_operation_evidence_contracts.py tests/integration/test_business_action_evidence.py -q`.
- [ ] **Implement mappings:** Cover authentication, directory/grant/role changes,
  mail/calendar, SharePoint/Teams, ARM Administrative activity, four Storage data
  planes and Key Vault. Record missing mappings honestly; fill all mappings required
  by the accepted business workflows. Source operation names and changed-property
  shapes from Microsoft references, not guessed camel-casing.
- [ ] **Implement capture/projection:** Add source facts inside successful mutation
  transactions; keep current job generation/lease fences and redaction. Read events
  use service-appropriate source identity, not client correlation ID as an
  idempotency credential. Complete known omitted Storage read diagnostics such as
  namespace ACL reads and snapshot listings where supported.
- [ ] **Verify:** New suites and evidence directory/authentication, mail/workload
  facts and `tests/integration/test_diagnostic_delivery.py` pass. Existing imports
  stay imported and do not trigger operational delivery.
- [ ] **Commit:** `feat: complete business operation evidence mappings`.

## B2. Authenticated Storage and Key Vault failure diagnostics

**Files:** Create `src/mockazure/evidence/{failures,failure_models}.py`,
`tests/integration/test_failed_data_plane_diagnostics.py`,
`tests/unit/test_failure_fact_redaction.py`.
Modify the existing Storage/Key Vault request/error boundaries and diagnostic
capture modules; add migration suffix `failed_operation_facts` if existing source
storage cannot retain required identity/outcome information.

**Interfaces:** `FailureFact(tenant_id: UUID, source_resource_id: str,
service: str, operation_key: str, request_id: UUID, occurred_at: datetime,
principal_id: UUID | None, status_code: int, error_code: str,
safe_properties: dict)` is an internal validated immutable value.
`capture_failure(database: Database, failure: FailureFact) -> UUID` opens its own
bounded transaction after the failed mutation transaction has rolled back.
`safe_properties` is operation-allowlisted; no request body/header dump.

- [ ] **Tests:** `test_permission_denial_survives_mutation_rollback`,
  `test_known_resource_validation_failure_is_logged`,
  `test_invalid_token_does_not_select_victim_tenant`,
  `test_foreign_resource_denial_does_not_disclose_resource`,
  `test_secret_and_sas_material_never_appears_in_diagnostics`,
  `test_failure_capture_does_not_replace_public_error`, and
  `test_one_request_has_one_native_failure_fact`.
  Enable diagnostics and verify the collected table/archive record as well as DB state.
  ```python
  assert public_response.status_code == 403
  assert stored_mutation is None
  assert diagnostic_source_ids == {failed_request_source_id}
  assert secret_value not in serialized_diagnostics
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_failure_fact_redaction.py tests/integration/test_failed_data_plane_diagnostics.py -q`.
- [ ] **Implement attribution:** Capture only after authenticated tenant identity and
  known source ownership have been resolved. Do not use arbitrary Host/path/token
  claims to manufacture tenant attribution. No diagnostic for unresolvable foreign
  targets. Honor existing diagnostic settings and category/destination lifecycle.
- [ ] **Implement delivery:** Map permission, supported validation and service errors
  to documented operation/status fields for Blob, Files, Tables, Queues and Key Vault.
  Preserve original HTTP error/status. Source persistence errors go to local runtime
  diagnostics without leaking request material or rewriting the public outcome.
- [ ] **Verify:** New tests pass plus data-plane authorization/diagnostic regression
  tests. Restart an owned worker with pending failure delivery to confirm existing
  durable semantics; this is a focused regression, not a new injected-fault mode.
- [ ] **Commit:** `feat: capture authenticated data-plane failure diagnostics`.

## B3. Cross-surface collection recipes and evidence qualification

**Files:** Extend `tools/verify_living_tenant.py`,
`src/mockazure/population/generators/recipes.py`,
`src/mockazure/profiles/data/data/business-azure-operations.json`,
and applicable business collaboration layers.
Create `tests/acceptance/test_living_evidence_workflows.py`,
`docs/living-tenant-evidence.md`, `docs/qualification/living-evidence.md`.
Update COVERAGE.md and relevant existing compatibility/results links.

**Interfaces:** A6 verifier gains `--phase evidence`; JSON report rows contain
operation key, source reference, collector/client version, expected and observed
IDs/counts, destination, status and explicit limitation. Public IDs and redacted
provenance suffice; never export credentials or raw privileged config.

- [ ] **Tests:** `test_action_to_collection_evidence_chain` covers one meaningful
  workflow for each B1 source family and all five B2 data-plane services.
  `test_imported_history_is_not_native_delivery` verifies provenance and no
  republished native event. `test_evidence_clock_is_separate_from_access_expiry`
  checks controlled evidence time does not prolong access.
  ```python
  assert report["missing_expected_ids"] == []
  assert native_sources_from_import == []
  assert access_token_expiry_after == access_token_expiry_before
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/acceptance/test_living_evidence_workflows.py -q`.
- [ ] **Implement recipes:** Actions use the normal named-identity HTTP adapters.
  Collect through Graph audit/sign-in, Exchange Search-UnifiedAuditLog, Management
  Activity feed, ARM Activity Logs, Blob archives and Logs queries as applicable.
  One action need not appear in every collector. Drain/wait using existing job
  state with a bounded deadline; do not replace missing evidence with fixtures.
- [ ] **Qualify:** Run `python -m tools.verify_living_tenant --config PATH --phase evidence --report work/living-evidence.json`.
  Require the applicable original clients and emitted operation mappings to agree.
  Record native/imported status and documented unavailable mappings per surface.
- [ ] **Verify/document:** Acceptance tests pass; document exact mappings, timestamps,
  redaction and visibility limits. Update coverage only for verified operations.
- [ ] **Commit:** `test: qualify connected native evidence collection`.
