# ARM providers and resource lifecycles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution is already selected. Steps use checkboxes; implement in this session.

**Goal:** Expose typed provider discovery, Storage/Key Vault management and coherent Compute/Network resource lifecycles.
**Architecture:** Extend the existing ArmResource inventory instead of creating a parallel inventory. Provider-specific validators and handlers share authorization, transactions, durable jobs and explicit event publication.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker; official Python, PowerShell, .NET and JavaScript/TypeScript clients.
**Spec:** [Approved cloud workflows specification](../specs/2026-10-18-cloud-workflows-design.md).
**Program:** [Execution index and shared acceptance contract](2026-10-18-cloud-workflows.md).
**Status:** B1 implemented and locally verified; B2-B4 pending. Native management client qualification remains the B4 gate.

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

- Case-insensitive ARM scope matching must not blur case-sensitive data-plane names (B1/B2).
- Unknown provider/version combinations must fail before mutating generic inventory (B1).
- Resource-group deletion racing with resource/data creation must leave no orphan authority (B2/B4).
- Provisioning jobs retried after lease expiry must not publish duplicate successful mutations (B4).
- Cross-tenant references and spoofed private-network headers must not grant access (B2/B3).

## File boundaries and contracts

New shared types in `cloud/types.py`: frozen `ResourceRef(tenant_id: UUID, scope: str, resource_type: str)`; `MutationResult(status: int, body: dict | None, headers: dict[str, str])`; `ServiceEvent(id: UUID, tenant_id: UUID, resource_id: str, service: str, category: str, operation: str, occurred_at: datetime, payload: dict)`. `cloud/events.py` persists events in the caller transaction; F3 consumes them. Domain handlers, not this envelope, determine public wire formats.

Provider roots are Microsoft.Storage, Microsoft.KeyVault, Microsoft.Compute, Microsoft.Network, and Microsoft.OperationalInsights (workspace management needed by F2). Add Monitor extension resources in F3. Provider catalog pins exact per-operation versions from Microsoft sources; unsupported versions fail, not silently downgrade.

## Task B1: Provider registry and current data-action authority

**Files**

- Create: `src/mockazure/cloud/types.py`, `src/mockazure/cloud/events.py`, `src/mockazure/cloud/models.py`, `src/mockazure/providers/arm/provider_registry.py`, `src/mockazure/providers/arm/provider_routes.py`, `src/mockazure/providers/arm/data_authorization.py`, `integrations/contracts/cloud/arm-providers.json`, `tests/integration/test_cloud_provider_registry.py`.
- Modify: `src/mockazure/providers/arm/contracts.py`, `src/mockazure/providers/arm/authorization.py`, `src/mockazure/providers/arm/rbac.py`, `src/mockazure/admin/app.py`, `tools/acquire_arm_contracts.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `resolve_operation(method: str, resource_id: str, api_version: str) -> dict`; `async require_data_action(session: AsyncSession, actor: PrincipalContext, scope: str, action: str) -> None`; `async append_service_event(session: AsyncSession, event: ServiceEvent) -> UUID`. Data authorization verifies the caller's service audience before evaluating current scoped dataActions/notDataActions, rather than calling the ARM-audience-only guard unchanged.

- [x] **Step 1 — Add the named failing scenarios.** test_provider_discovery_and_version lists supported resourceTypes/versions, changes registration state and reads it back; rejects an unknown namespace/version. test_actions_are_not_data_actions grants Contributor and verifies management write but data denial, then adds a data role and verifies data access. test_scoped_role_union checks exclusions within each role before union.

```python
assert 'Microsoft.Storage' in registered_names
assert unsupported.status_code == expected_contract_error
assert management_allowed is True and data_allowed is False
assert role_union_result == expected_effective_actions
```

- [x] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_cloud_provider_registry.py tests/integration/test_arm_authorization.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [x] **Step 3 — Implement the declared interfaces.** Pin provider metadata, built-in data roles and action patterns; bind registered operations to real handlers and prevent catch-all success. Add provider discovery/registration operations as the working deliverable. Define shared types/event outbox and indexes, retaining existing ARM fact publication. Test scoped union semantics and audience confusion. Register routers with correct service error dispatch.
- [x] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [x] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add typed ARM providers and data-action authorization"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task B2: Storage account resources and local endpoints

**Files**

- Create: `src/mockazure/storage/accounts.py`, `src/mockazure/storage/models.py`, `src/mockazure/storage/endpoints.py`, `src/mockazure/providers/arm/storage_routes.py`, `tests/integration/test_storage_account_management.py`.
- Modify: `src/mockazure/providers/arm/provider_registry.py`, `src/mockazure/providers/arm/inventory.py`, `src/mockazure/endpoints.py`, `src/mockazure/config.py`, `src/mockazure/admin/app.py`, `src/mockazure/identity/tokens.py`, `src/mockazure/oauth/scopes.py`, `src/mockazure/authorization/catalog.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async mutate_account(session: AsyncSession, actor: PrincipalContext, resource_id: str, operation: str, body: dict, now: datetime) -> MutationResult`; `async resolve_account(session: AsyncSession, tenant_id: UUID, name: str) -> ResourceRef`; `service_endpoint(account: str, service: str, profile: dict) -> str`. Credentials are held in separate secret-bearing records, never generic ArmResource JSON.

- [ ] **Step 1 — Add the named failing scenarios.** test_account_lifecycle_and_keys creates/updates/list/gets/deletes an account, rotates one of two keys and verifies the other remains valid. test_account_endpoint_isolation checks each service URL is local and maps to the correct account. test_network_context rejects client-forged network claims and distinguishes stored configuration from executable local rules.

```python
assert set(inventory_ids) >= {created['id']}
assert old_key1_valid is False and old_key2_valid is True
assert account_get_contains_keys is False
assert all(url in configured_local_endpoints for url in returned_endpoints)
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_storage_account_management.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Implement pinned account CRUD/list/check-name availability, listKeys/regenerateKey and selected service properties. Route Blob/Files/Table/Queue on distinct local service origins with development-style account path addressing, explicitly documented as a local endpoint adaptation. Validate signing and URL construction with all four SDK ecosystems in C1; do not rewrite Microsoft resource payload schemas to conceal the adapter. Extend EndpointProfile/resource audiences, OAuth scope resolution and permission catalogs without breaking old profiles; prove an original credential client can acquire a locally issued token for each supported new data-service audience. Define account lifecycle locks, quotas and credential export exclusions.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add Storage account management and local service endpoints"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task B3: Key Vault, workspace, Compute and Network inventory

**Files**

- Create: `src/mockazure/providers/arm/resource_handlers.py`, `src/mockazure/providers/arm/resource_relationships.py`, `src/mockazure/keyvault/vaults.py`, `src/mockazure/keyvault/models.py`, `src/mockazure/monitor/workspaces.py`, `tests/integration/test_cloud_resource_inventory.py`.
- Modify: `src/mockazure/providers/arm/provider_registry.py`, `src/mockazure/providers/arm/models.py`, `src/mockazure/providers/arm/inventory.py`, `src/mockazure/providers/arm/facts.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async mutate_resource(session: AsyncSession, actor: PrincipalContext, ref: ResourceRef, operation: str, body: dict, now: datetime) -> MutationResult`; `async validate_references(session: AsyncSession, ref: ResourceRef, body: dict) -> None`. Provider-specific validators dispatch from the pinned catalog; vault/workspace state is authoritative for D3/F2.

- [ ] **Step 1 — Add the named failing scenarios.** test_related_compute_network_inventory builds VNet/subnet, NSG/rule, NIC/public IP, disk, VM and extension records and compares provider reads with generic inventory. test_vault_workspace_authority creates a vault/workspace and verifies canonical IDs and settings. Cross-tenant and deleted parent references must fail atomically; changing unsupported locations/SKUs fails by declared profile.

```python
assert provider_resource['id'] in inventory_ids
assert vm_nic_id == nic['id']
assert invalid_reference.status_code == expected_reference_error
assert inventory_after_failed_write == inventory_before
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_cloud_resource_inventory.py tests/integration/test_arm_inventory.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Acquire actual resource schema closure, implement create/read/update/delete/list for named families and supported child resources. Add vault RBAC/access-policy mode settings and workspace provisioning; apply mode-specific authority later in D3. Compute extensions store modeled state without running scripts. Persist relationships with tenant/subscription invariants and operation-specific schema validation; emit native Activity Log facts and domain events.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add vault workspace compute and network resources"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task B4: Asynchronous lifecycle, deletion and management clients

**Files**

- Create: `src/mockazure/providers/arm/provider_jobs.py`, `src/mockazure/cloud/lifecycle.py`, `tests/integration/test_cloud_resource_lifecycle.py`, `integrations/powershell/Test-CloudManagement.ps1`, `integrations/python/cloud_management.py`.
- Modify: `src/mockazure/admin/cloud_routes.py` (providerState already added by B1), `src/mockazure/providers/arm/operations.py`, `src/mockazure/jobs/dispatch.py`, `src/mockazure/jobs/exports.py`, `src/mockazure/admin/maintenance_routes.py`, `src/mockazure/admin/app.py`, `src/mockazure/evidence/cleanup.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async process_provider_operation(database: Database, claim: JobClaim, wall_now: datetime) -> JobResult`; `async reset_cloud(session: AsyncSession, tenant_id: UUID, expected_generation: int) -> None`; `async export_cloud(session: AsyncSession, tenant_id: UUID) -> dict`; `async import_cloud(session: AsyncSession, tenant_id: UUID, document: dict) -> None`; `async prune_cloud(session: AsyncSession, tenant_id: UUID, now: datetime, limit: int) -> int`. Domain-owned lifecycle contributors are registered explicitly, not found by untrusted imports.

- [ ] **Step 1 — Add the named failing scenarios.** test_provider_job_fencing exercises VM start/restart/powerOff/deallocate and resource deletion with a stale worker lease. test_delete_races_create holds the parent deletion lock while attempting child creation. test_cloud_export_restore restores relationships and checks credential exclusion; corrupted cross-tenant references reject the whole import.

```python
assert stale_result.state == 'stale'
assert final_instance_state == expected_instance_state
assert successful_event_count == 1
assert orphan_rows == 0
assert leaked_secret_values == []
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_cloud_resource_lifecycle.py tests/integration/test_arm_delete_jobs.py tests/integration/test_arm_upgrade.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Implement official operation/status/result polling envelopes and retry headers, generation fences and simulated instanceView transitions. Coordinate group deletion with each data-plane's retention/delete rules; D3 supplies vault recoverability hooks. Add typed admin provisioning/export/reset and migration tests from baseline head 052. Qualify management clients through G1's shared harness, introduced alongside these workflows.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: make cloud resource lifecycles durable and queryable"`. Preserve unrelated work. Push/review according to the program's phase PR rules.
