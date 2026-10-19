# Tables Queues and Key Vault secrets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution is already selected. Steps use checkboxes; implement in this session.

**Goal:** Provide stateful structured data, work queues and versioned secret workflows with service-specific authorization.
**Architecture:** Reuse Storage request authentication and ARM resource authority while keeping each service's persistence and wire contract separate. Key Vault uses its own audience, access-policy/RBAC decision and recoverable secret state.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker; official Python, PowerShell, .NET and JavaScript/TypeScript clients.
**Spec:** [Approved cloud workflows specification](../specs/2026-10-18-cloud-workflows-design.md).
**Program:** [Execution index and shared acceptance contract](2026-10-18-cloud-workflows.md).
**Status:** Proposed implementation plan; no tasks executed.

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

- Table EDM integers/binary/date values must round-trip without JSON precision loss (D1).
- A failing same-partition batch must roll back every entity mutation (D1).
- Queue redelivery must invalidate stale pop receipts and preserve dequeue counts (D2).
- Vault permission-mode switches must immediately stop obsolete grants (D3).
- Secret values must remain absent from evidence, logs and default exports (D4).

## File boundaries and contracts

Depends on B and C1; does not depend on Blob/Files completion except common content/transport primitives. Pin Table OData/EDM rules, Queue message XML/encoding rules and the versioned Key Vault secrets contract independently.

## Task D1: Typed Table entities and transactions

**Files**

- Create: `src/mockazure/storage/tables.py`, `src/mockazure/storage/table_models.py`, `src/mockazure/storage/table_query.py`, `src/mockazure/providers/storage/table_routes.py`, `tests/integration/test_table_workflows.py`, `tests/unit/test_table_edm.py`.
- Modify: `src/mockazure/providers/storage/routes.py`, `src/mockazure/cloud/lifecycle.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async mutate_entity(session: AsyncSession, access: StorageAccess, table: str, partition: str, row: str, operation: str, body: dict, etag: str | None, now: datetime) -> dict`; `async transact_entities(session: AsyncSession, access: StorageAccess, table: str, operations: list[dict], now: datetime) -> list[dict]`; `parse_table_filter(text: str) -> object` returns an internal typed AST owned by table_query.py, never raw SQL.

- [ ] **Step 1 — Add the named failing scenarios.** test_table_edm_and_queries persists Int64 beyond JavaScript's exact-number range using the documented EDM encoding, binary/date values and escaped keys, then filters/projects/pages. test_table_batch_rollback makes the second operation fail; no entity changes. test_table_etag_race allows one conditional update. Cross-partition batches reject before writes.

```python
assert decoded_int64 == 9007199254740993
assert entity_after_failed_batch == entity_before
assert len(all_keys) == len(set(all_keys))
assert stale_update.status_code == 412
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/unit/test_table_edm.py tests/integration/test_table_workflows.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Implement table lifecycle, typed entity CRUD/merge/upsert, service-specific query operators and projection, opaque continuations and multipart batch request/response framing. Enforce sourced transaction/count/size limits and explicit supported EDM types. Bind tokens to account/table/query and reauthorize. Use parameterized SQL and tenant/partition indexes; include durable metadata, events and lifecycle adapters.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add Azure Table entity workflows"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task D2: Queue visibility and delivery lifecycle

**Files**

- Create: `src/mockazure/storage/queues.py`, `src/mockazure/storage/queue_models.py`, `src/mockazure/providers/storage/queue_routes.py`, `tests/integration/test_queue_workflows.py`.
- Modify: `src/mockazure/providers/storage/routes.py`, `src/mockazure/cloud/lifecycle.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async receive_messages(session: AsyncSession, access: StorageAccess, queue: str, count: int, visibility_seconds: int, now: datetime) -> list[dict]`; `async update_message(session: AsyncSession, access: StorageAccess, queue: str, message_id: str, pop_receipt: str, text: str | None, visibility_seconds: int, now: datetime) -> dict`; `async delete_message(session: AsyncSession, access: StorageAccess, queue: str, message_id: str, pop_receipt: str, now: datetime) -> None`.

- [ ] **Step 1 — Add the named failing scenarios.** test_queue_redelivery_and_receipts enqueues, peeks, receives, advances the explicit test clock, receives again, denies the first receipt and deletes with the current one. test_queue_workers_and_ttl runs two receivers and expires a message while preserving correct metadata. Raw text/base64 differences are tested under each pinned SDK encoding setting.

```python
assert first['DequeueCount'] == 1 and second['DequeueCount'] == 2
assert first['PopReceipt'] != second['PopReceipt']
assert stale_delete.status_code == expected_receipt_error
assert concurrent_visible_message_ids_are_disjoint is True
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_queue_workflows.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Implement queue CRUD/metadata, enqueue/peek/get/update/delete/clear with row locks and source-derived expiry/visibility limits. Do not make reads destructive or mistake peek for receive. Preserve queue XML/error/header behavior and restart-safe receipt state. Emit eligible operation facts without leaking message payloads by default; integrate lifecycle and cleanup.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: simulate Queue delivery and visibility"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task D3: Vault secret versions and authorization modes

**Files**

- Create: `src/mockazure/keyvault/secrets.py`, `src/mockazure/keyvault/authorization.py`, `src/mockazure/keyvault/secret_models.py`, `src/mockazure/providers/keyvault/routes.py`, `src/mockazure/providers/keyvault/errors.py`, `integrations/contracts/cloud/keyvault.json`, `tests/integration/test_keyvault_secrets.py`.
- Modify: `src/mockazure/keyvault/vaults.py`, `src/mockazure/endpoints.py`, `src/mockazure/config.py`, `src/mockazure/admin/app.py`, `src/mockazure/cloud/lifecycle.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async require_secret_access(session: AsyncSession, actor: PrincipalContext, vault_id: str, secret_name: str | None, operation: str) -> None`; `async mutate_secret(session: AsyncSession, actor: PrincipalContext, vault_id: str, name: str, version: str | None, operation: str, body: dict, now: datetime) -> dict`; `async get_secret(session: AsyncSession, actor: PrincipalContext, vault_id: str, name: str, version: str | None, now: datetime) -> dict`.

- [ ] **Step 1 — Add the named failing scenarios.** test_secret_versions_and_attributes creates two versions, retrieves latest and explicit older version, changes attributes, checks list payloads omit values, and verifies disabled/not-yet-valid/expired behavior from the pinned contract. test_vault_auth_mode_switch revokes access across RBAC/access-policy transitions and rejects an ARM token on the secret endpoint.

```python
assert first['id'] != second['id']
assert explicit_old['value'] == first_value
assert latest['id'] == second['id']
assert all('value' not in item for item in list_result)
assert revoked_read.status_code == 403
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_keyvault_secrets.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Pin Key Vault management/data API versions and permission rules. Implement secret set/get/list/update/list versions with coherent IDs/local endpoints, current authorization, metadata and eligibility checks. Add separate Key Vault resource audience and client challenge flow. Store values encrypted using existing cryptographic key-management patterns extended for secrets; ordinary logs/errors/evidence never include values. Define explicit backup credentials separately from tenant evidence.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add authorized Key Vault secret versions"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task D4: Recovery, purge protection and structured-service qualification

**Files**

- Create: `tests/integration/test_structured_service_lifecycle.py`, `integrations/python/storage_structured.py`, `integrations/python/keyvault_secrets.py`, `integrations/powershell/Test-StructuredServices.ps1`.
- Modify: `src/mockazure/keyvault/secrets.py`, `src/mockazure/cloud/lifecycle.py`, `src/mockazure/admin/cloud_routes.py`, `src/mockazure/admin/maintenance_routes.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Extend D3 mutation operations with delete, recover and purge; add deleted-secret list/get. Integrate Tables/Queues/secrets with B4 lifecycle functions. Default export contains safe metadata only; explicitly requested encrypted secret backups require an explicit restore key reference and a versioned secret-bearing manifest.

- [ ] **Step 1 — Add the named failing scenarios.** test_secret_recovery_and_protection deletes and recovers all versions, rejects protected purge, tests retention using a controlled clock and verifies vault/resource deletion consistency. test_structured_restore_and_isolation round-trips table/queue state, checks no unauthorized secret values in default export, and rejects cross-tenant references.

```python
assert recovered_versions == original_versions
assert protected_purge.status_code == expected_protection_error
assert secret_value not in serialized_default_export
assert restored_entities == original_entities
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_structured_service_lifecycle.py tests/integration/test_table_workflows.py tests/integration/test_queue_workflows.py tests/integration/test_keyvault_secrets.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Implement recoverability and retention rules, encrypted explicit backups, current vault authority and maintenance hooks. Qualify all four ecosystems' supported table/queue/secret workflows, showing explicit REST fallback where a PowerShell operation lacks a native cmdlet. Use local credentials only and update operation-level coverage with negative and recovery evidence.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "test: qualify Table Queue and secret lifecycle workflows"`. Preserve unrelated work. Push/review according to the program's phase PR rules.
