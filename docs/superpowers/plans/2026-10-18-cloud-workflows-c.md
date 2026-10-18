# Blob and Azure Files REST data planes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution is already selected. Steps use checkboxes; implement in this session.

**Goal:** Make official Storage clients perform authenticated, persistent Blob and Files REST workflows.
**Architecture:** Use service-specific REST adapters over canonical accounts and immutable content versions. Authorization, signed requests, leases and preconditions execute before transactional changes; public transport remains distinct from internal upload APIs.

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

- Signing canonicalization must preserve repeated/encoded headers and query fields across SDKs (C1).
- Range writes crossing chunk boundaries must preserve untouched bytes and snapshots (C3).
- Concurrent conditional writes must allow only the matching current ETag (C2/C3).
- Expired leases, SAS and delegated keys must not remain effective through retries (C1/C2).
- Deletion and content pruning must preserve pinned versions and invalidate stale handles (C4).

## File boundaries and contracts

Depends on B1/B2 and the existing content store. New `storage/types.py` defines `StorageAccess(tenant_id: UUID, account_id: str, mode: str, principal: PrincipalContext | None, permissions: frozenset[str])`, `ContentVersion(id: UUID, content_id: UUID, etag: str, size: int)`. Shared Key/SAS identities must never be represented as forged OAuth principals.

Local endpoint adaptation: separate Blob/Files/Table/Queue origins with account path addressing; original service-relative operations, signing and payloads remain qualified. C1 must prove this adapter with official clients before building on it. If an SDK cannot configure this shape, implement an explicit local origin binding for that client/service and document the adapter; never patch SDK request/response semantics.

## Task C1: Storage authentication, endpoint routing and container/share creation

**Files**

- Create: `src/mockazure/storage/types.py`, `src/mockazure/storage/containers.py`, `src/mockazure/storage/shares.py`, `src/mockazure/storage/authorization.py`, `src/mockazure/storage/signing.py`, `src/mockazure/storage/sas.py`, `src/mockazure/providers/storage/routes.py`, `src/mockazure/providers/storage/errors.py`, `integrations/contracts/cloud/storage.json`, `tests/unit/test_storage_signing.py`, `tests/integration/test_storage_authorization.py`.
- Modify: `src/mockazure/storage/accounts.py`, `src/mockazure/endpoints.py`, `src/mockazure/config.py`, `src/mockazure/admin/app.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async authorize_storage(session: AsyncSession, request: Request, service: str, action: str, resource: str, now: datetime) -> StorageAccess`; `canonicalize_storage_request(method: str, raw_path: bytes, query: list[tuple[str, str]], headers: list[tuple[str, str]], account: str, service: str, version: str) -> bytes`. Also produce `async create_container(session: AsyncSession, access: StorageAccess, name: str, properties: dict, now: datetime) -> dict` in containers.py and `async create_share(session: AsyncSession, access: StorageAccess, name: str, properties: dict, now: datetime) -> dict` in shares.py; C2/C3 consume these operations.

- [ ] **Step 1 — Add the named failing scenarios.** test_signed_container_share_roundtrip uses all four official clients with local endpoints; create/list a container and share with account keys. test_storage_auth_boundaries exercises tampered signatures, start/expiry edges, SAS resource/action restriction, key rotation and OAuth audience confusion. Files OAuth must satisfy documented request-intent and backup-semantics rights, not just a Blob role.

```python
assert valid_signature is True and tampered_signature is False
assert expired.status_code == 403
assert contributor_only_data_request.status_code == 403
assert four_client_roundtrips == {'python', 'powershell', 'dotnet', 'typescript'}
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/unit/test_storage_signing.py tests/integration/test_storage_authorization.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Pin version-specific authorization and REST contracts. Implement Shared Key/Shared Key Lite variants only where required, account/service SAS and Blob user delegation key/SAS, selected stored access policies and public Blob read settings. Validate current account flags and grants on each request. Use service-specific XML/JSON error serialization and header processing. Add network/permission deny tests and supported local TLS/endpoints configuration; no host trust/DNS changes.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: authenticate Storage requests from official clients"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task C2: Blob bytes, blocks, conditions and version lifecycle

**Files**

- Create: `src/mockazure/storage/blob.py`, `src/mockazure/storage/blob_models.py`, `src/mockazure/storage/leases.py`, `src/mockazure/providers/storage/blob_routes.py`, `src/mockazure/content/stream_upload.py`, `tests/integration/test_blob_workflows.py`.
- Modify: `src/mockazure/content/repository.py`, `src/mockazure/providers/storage/routes.py`, `src/mockazure/cloud/lifecycle.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async put_blob(session: AsyncSession, access: StorageAccess, container: str, name: str, upload_id: UUID, conditions: dict, now: datetime) -> ContentVersion`; `async begin_stream_upload(session: AsyncSession, tenant_id: UUID, expected_length: int | None, max_size: int) -> UUID`; `async append_stream_chunk(session: AsyncSession, tenant_id: UUID, upload_id: UUID, data: bytes) -> None`; `async complete_stream_upload(session: AsyncSession, tenant_id: UUID, upload_id: UUID, media_type: str) -> ContentRef`. The adapter computes hash/size server-side when Azure clients do not provide SHA-256.

- [ ] **Step 1 — Add the named failing scenarios.** test_blob_block_and_version_roundtrip uploads out-of-order blocks and commits a specified order, checks full/ranged bytes and listing continuation. test_blob_conditional_race issues two writes with one old ETag and expects one winner. test_blob_lease_snapshot_delete exercises acquire/renew/release/break, version reads, snapshot retention, soft-delete/recovery and pruning.

```python
assert downloaded == expected_bytes
assert sha256(downloaded).hexdigest() == expected_sha256
assert sorted(write_statuses) == [201, 412]
assert snapshot_bytes_after_update == original_bytes
assert len(listed_names) == len(set(listed_names))
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_blob_workflows.py tests/integration/test_content_storage.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Implement container and Blob operations in the approved profile, metadata/properties, get/put block lists, versions/snapshots/soft-delete, prefix/delimiter/continuation, Range and service-correct preconditions. Keep source bytes immutable; use generation-bound page handles and transactional leases/version changes. Add stream staging without requiring the admin protocol's client-supplied hash or materializing entire bodies. Emit secret-free service facts and integrate lifecycle ownership.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add persistent Blob data workflows"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task C3: Files REST directories, ranges and snapshots

**Files**

- Create: `src/mockazure/storage/files.py`, `src/mockazure/storage/file_models.py`, `src/mockazure/providers/storage/file_routes.py`, `tests/integration/test_files_rest_workflows.py`.
- Modify: `src/mockazure/providers/storage/routes.py`, `src/mockazure/cloud/lifecycle.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async write_file_range(session: AsyncSession, access: StorageAccess, share: str, path: str, start: int, data: bytes | None, length: int, conditions: dict, now: datetime) -> ContentVersion`. `data=None` clears the selected range. Consume C2 stream staging and immutable content references; define sparse/range metadata in file_models.py.

- [ ] **Step 1 — Add the named failing scenarios.** test_file_cross_chunk_ranges creates nested directories and a file larger than 1 MiB, writes across the boundary, clears a subrange, resizes and reads ranges. test_file_snapshot_and_conditions modifies the live file after a share snapshot and proves snapshot bytes unchanged. test_file_paths_and_metadata checks Unicode, escaped separators, metadata and documented directory deletion rules.

```python
assert actual_bytes == expected_sparse_bytes
assert cleared_slice == bytes(clear_length)
assert snapshot_bytes == pre_change_bytes
assert stale_write.status_code == 412
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_files_rest_workflows.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Implement share/directory/file CRUD, list/continuation, file properties/metadata, range update/clear/read/list and supported share snapshots/access conditions. Pin exact naming/case, size/range and Files permission/header rules; do not reuse Blob path validation blindly. Stream copy-on-write affected content with metadata locks; reject invalid ranges atomically. No SMB/NFS listeners or file mounts.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add Azure Files REST data workflows"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task C4: Storage durability, diagnostic facts and client gates

**Files**

- Create: `tests/integration/test_storage_lifecycle.py`, `integrations/python/storage_binary.py`, `integrations/powershell/Test-StorageBinary.ps1`.
- Modify: `src/mockazure/cloud/lifecycle.py`, `src/mockazure/admin/cloud_routes.py`, `src/mockazure/jobs/exports.py`, `src/mockazure/admin/maintenance_routes.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Contribute Blob/Files lifecycle adapters to B4's `reset_cloud`, `export_cloud`, `import_cloud`, `prune_cloud`. Publish `ServiceEvent` with source operation/account/resource/outcome and no keys/SAS signatures. Extend shared .NET/TypeScript runner workflows `storage-binary` defined by G1.

- [ ] **Step 1 — Add the named failing scenarios.** test_storage_restart_restore streams a 256 MiB object, restarts, exports/restores data and compares hashes/metadata. test_storage_reset_fences_old_work starts upload/paging before reset and rejects stale continuation/completion after reset. test_storage_diagnostic_fact_once retries a committed operation and checks source fact identity rather than treating every delivery attempt as a new action.

```python
assert restored_sha256 == source_sha256
assert full_object_materializations == 0
assert old_generation_write_allowed is False
assert exported_credential_values == []
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_storage_lifecycle.py tests/integration/test_blob_workflows.py tests/integration/test_files_rest_workflows.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Finish lifecycle and data cleanup hooks, explicit local resource limits, concurrency/streaming tests and actual client transcripts. Add A/B/C-aware coverage rows for management versus data permission and per-operation auth modes. Expose genuine limitations; no family-level claim from a single upload test.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "test: qualify Blob and Files persistence and clients"`. Preserve unrelated work. Push/review according to the program's phase PR rules.
