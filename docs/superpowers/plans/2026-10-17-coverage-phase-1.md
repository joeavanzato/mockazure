# Coverage phase 1 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.
> Native execution is selected. Steps use checkboxes; no implementation agents.

**Goal:** Close directory/Exchange collection gaps and qualify common query behavior.
**Architecture:** Extend canonical directory/security/mail stores and operation-level
contracts. Reuse current authorization, typed SQL queries and durable continuations.
**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker, PowerShell.
**Spec:** [Approved program design](../specs/2026-10-17-coverage-expansion-design.md).
**Status:** Implemented and qualified in PRs #15–#17; awaiting merge. The checklist below preserves the original execution plan.

Current implementation and qualification evidence: [coverage results](../../coverage-expansion-results.md).

## Global Constraints

- Work only in MockAzure; never modify VTW or copy its secrets, generated files or untracked files.
- No real-tenant traffic; use official public Microsoft documentation and pinned schema sources.
- Native execution; commit cohesive changes, push reviewable PRs, do not auto-merge.
- Graph additions target v1.0 contracts; do not infer beta support from a shared router.
- No host trust, DNS or firewall changes; all generated service/content URLs stay local.
- Current COVERAGE.md describes shipped behavior; planned features remain separately labeled.
- Preserve existing data and accepted collector limitations; no silent destructive migration.

## Review Focus

- Heterogeneous object IDs: apply type-specific visibility before returning getByIds results (1A).
- Nested/cyclic group edges: no duplicate objects or infinite traversal (1A).
- Permission revocation between batch pages: fresh authorization for every inner call (1B).
- Concurrent throttling across instances: one shared quota decision per operation (1B).
- Exchange display identity collisions: permission rows resolve canonical tenant/mailbox IDs (1C).

## Files and contracts

Existing implementation remains in `src/mockazure`. All file paths below are repo-relative.
New modules are explicitly marked; omitted package __init__.py files are empty exports.
Register new routers in `src/mockazure/admin/app.py` using the existing application pattern.
Allocate migration IDs sequentially after the then-current Alembic head; the descriptive
migration filename specified below must be retained after that numeric prefix.

Each operation record in new `integrations/contracts/coverage-operations.json` has
`service`, `version`, `method`, `path`, `sourceUrl`, `sourceSha256`, `retrievedAt`,
`requestType`, `responseType`, `permissions`, `queryOptions`, `limits`, `tests`, and
`status`. Status is planned/implemented/qualified. Never infer qualification from a
family label. Extend `tools/collector_coverage.py` to match real method/path/parameters.
Later phases append to this same manifest and use the same source-verification test.

### Task 1A: directory lookup, relationships and richer reads

**Files:** Modify `src/mockazure/providers/graph/directory.py`, `relationships.py`,
`permissions.py`, `src/mockazure/directory/repository.py`, `references.py`, `mutations.py`,
`src/mockazure/providers/graph/security/role_routes.py`, `src/mockazure/security/roles.py`,
`src/mockazure/admin/directory_routes.py`, `src/mockazure/authorization/catalog.py`.
Create `src/mockazure/providers/graph/object_routes.py`,
`src/mockazure/directory/organization.py`, the operation manifest above,
`tests/unit/test_coverage_operation_contracts.py`,
`tests/integration/test_directory_collection_completion.py`.
Modify schema extraction and `src/mockazure/providers/graph/schemas/v1.0.json`;
add migration `directory_collection_completion.py` if persisted organization/edges need columns.

**Interfaces:** Consume canonical directory/security IDs and `authorize(context,
operation, session)`. New `resolve_objects(session, context, ids: list[str],
types: list[str] | None) -> list[dict]` in object_routes uses existing serializers,
never returns stored secrets. `organization.py` owns typed optional organization fields;
role permission descriptions remain descriptive, not a new custom-role evaluator.

- [ ] Pin v1.0 REST operations and CSDL type closure for the Phase 1 directory scope;
      record permission differences, getByIds maximum and limited-information behavior.
- [ ] Add failing `test_get_by_ids_mixed_visibility`, `test_relationship_cycles_and_paging`,
      `test_owner_and_registered_user_reference_roundtrip`, `test_assignment_navigation_parity`,
      `test_me_alias_requires_delegated_identity` and `test_organization_role_fields_roundtrip`.
      Assert exact IDs/types, no duplicates, correct permission-limited projections, atomic
      failed references, and no ownership change to OAuth grants.
- [ ] Run `uv run pytest tests/integration/test_directory_collection_completion.py -q`;
      confirm failures identify missing behavior rather than a database/setup failure.
- [ ] Implement generic object lookup, group membership, ownedObjects, registeredUsers,
      user/group appRoleAssignments reads and /me aliases. Add only the owner/registered-user
      $ref writes specified in the design, with shared relationship history and audit facts.
- [ ] Add typed organization provisioning and richer built-in role reads. Integrate new state
      with tenant reset/export and migration; preserve existing response fields.
- [ ] Run the targeted tests plus directory/security integration regressions. Use original
      Graph SDK and PowerShell to resolve mixed IDs and walk all pages. Update COVERAGE.md,
      contract manifest and collector mapping; commit, push and open/attach the 1A PR.

### Task 1B: query, batch and throttling consistency

**Files:** Modify `src/mockazure/providers/graph/query/contracts.py`, `compiler.py`,
`src/mockazure/providers/graph/evidence/query.py`, `src/mockazure/providers/graph/batch.py`,
`throttle.py`, `src/mockazure/collections/contracts.py`, `routes.py`,
`tools/collector_coverage.py`; create `tests/integration/test_collection_protocol_completion.py`,
`tests/unit/test_coverage_operation_contracts.py` additions and
`integrations/powershell/Test-CoverageCollections.ps1`.

**Interfaces:** Keep resource-specific query contracts. Expose
`async enforce_read_budget(session, context, operation_key: str, now: datetime) -> None`
in throttle.py; raises the existing Graph error with Retry-After. Each inner batch
operation consumes one read decision; the outer envelope has no duplicate read charge.
Later workload routers call this function after authentication and before storage reads.

- [ ] Add operation-by-operation query matrices to the manifest from official REST pages:
      sign-in/directory-audit selection/count where documented, registration-report queries,
      supported security collection ordering/selection, and existing relationship expansions.
      Unavailable Microsoft options stay unsupported; do not force one common option set.
- [ ] Add failing tests for selected nested fields without secret leakage, invalid query
      properties, missing advanced-query headers, empty count=0, snapshot changes, tampered
      cursors, revoked access, two-instance 429 and batch item-specific errors. Assert 429
      contains a usable Retry-After and a successful retry returns the same expected IDs.
- [ ] Run `uv run pytest tests/integration/test_collection_protocol_completion.py -q` red.
- [ ] Extend typed query contracts/compiler only for pinned operations; add bounded nested
      expansion paging where the operation specifies it. Make read-only batch dispatch reuse
      normal operation authorization and validation for added roots. Reject external URLs,
      writes and dependencies explicitly.
- [ ] Add shared PostgreSQL budget/injection handling without removing existing special
      provider quotas. Extend configuration/capability metadata and migrations as required.
- [ ] Run targeted tests and existing Graph batch/query/continuation regressions, then the
      PowerShell collection script. Record the specific request/query combinations qualified.
      Update coverage, commit and publish the 1B PR.

### Task 1C: Exchange permission and configuration evidence

**Files:** Create `src/mockazure/mail/configuration.py`, `permission_records.py`,
`tests/integration/test_exchange_collection_completion.py` and migration
`exchange_collection_completion.py`. Modify `src/mockazure/mail/models.py`,
`src/mockazure/admin/mail_routes.py`, `src/mockazure/providers/exchange/commands.py`,
`serialization.py`, `bootstrap.py`, `src/mockazure/mail/authorization.py`,
`integrations/contracts/exchange-mail.json`, `tools/qualify_mail_collectors.py`.

**Interfaces:** `async read_configuration(session, tenant_id: UUID, command: str,
parameters: dict) -> list[dict]` and `async read_mailbox_permissions(session,
context, mailbox_user_id: UUID, recipient: bool) -> list[dict]`. The command dispatcher
performs existing command authorization; permission reads additionally enforce scope.
Permission evidence is not an ExchangeCommandGrant and cannot confer command authority.

- [ ] Pin official command parameter/output references for Get-MailboxPermission,
      Get-RecipientPermission, Get-TransportRule, Get-TransportConfig, Get-AcceptedDomain
      and enriched Get-OrganizationConfig. Define bounded accepted parameters in exchange-mail.json.
- [ ] Add failing tests for canonical identity versus aliases, inherited/deny permission rows,
      invalid parameter rejection, grant revocation, mailbox scope and configuration persistence.
      Assert unrelated mailboxes remain inaccessible and data survives API replacement.
- [ ] Run `uv run pytest tests/integration/test_exchange_collection_completion.py -q` red.
- [ ] Add tenant-scoped typed configuration imports and permission records; expose the six
      read commands through the existing approved server-generated proxy mechanism. Extend
      lifecycle/export/upgrade behavior without executing transport rules or arbitrary commands.
- [ ] Run targeted tests and existing Exchange regression tests. Qualify commands through the
      original ExchangeOnlineManagement module and pinned collectors where invoked; record
      omitted parameters and upstream failures explicitly. Update coverage and publish the 1C PR.

## Per-slice completion gate

- [ ] Run `uv run ruff check .` and `uv run pytest tests/unit -q`.
- [ ] Prepare the isolated database with the existing `tools/prepare_tests.py` and
      `compose.test.m6.yaml`; run relevant integration tests with the actual configured
      MOCKAZURE_TEST_DB_PORT (CI uses 55445). Never reuse a non-test database.
- [ ] Review diff against the spec, validate documentation links, preserve existing compatibility
      exceptions, push and require all CI jobs. Report failures; do not mark the next phase done.
