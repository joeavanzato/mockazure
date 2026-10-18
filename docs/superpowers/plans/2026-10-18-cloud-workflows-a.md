# Exchange audit and security workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution is already selected. Steps use checkboxes; implement in this session.

**Goal:** Make the original Exchange module execute audit search, recipient/delegation and security configuration workflows against persistent local state.
**Architecture:** Keep the existing command dispatcher and compiled module transport. Split new audit, recipient and security behavior into focused modules; use canonical mailbox/evidence stores and current authorization.

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

- Date-only/offset timestamps and session reuse must not skip audit records (A1).
- Revocation between search pages must stop access without consuming another principal's cursor (A1).
- Recipient display-name collisions must not resolve across tenants or grant unintended delegation (A2).
- PowerShell switch/array/null serialization must match actual parameter sets (A3).
- Role/configuration mutations must not invent OAuth grants or external transport (A4).

## File boundaries and contracts

Existing seams: `invoke_command(session, actor, name, parameters, now)` in providers/exchange/commands.py, `PARAMETERS` and `fixed_proxy_source` in bootstrap.py, mail/configuration.py, mail/permission_records.py, auditlog/authorization.py, auditlog/queries.py and EvidenceVersion. Preserve the accepted original-MES serialization limitation.

Contract sources: official Exchange Online command pages and pinned original ExchangeOnlineManagement module. Derive exact cloud parameter sets before handler implementation. Do not pass a Graph operation schema off as a PowerShell command schema.

## Task A1: Durable Search-UnifiedAuditLog through the original module

**Files**

- Create: `src/mockazure/mail/unified_audit.py`, `src/mockazure/mail/unified_audit_models.py`, `tools/acquire_cloud_contracts.py`, `src/mockazure/cloud/contracts.py`, `integrations/contracts/cloud/exchange.json`, `tests/unit/test_cloud_contracts.py`, `tests/integration/test_exchange_unified_audit.py`.
- Modify: `src/mockazure/providers/exchange/bootstrap.py`, `src/mockazure/providers/exchange/commands.py`, `src/mockazure/mail/authorization.py`, `src/mockazure/mail/contracts.py`, `src/mockazure/mail/schemas.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `cloud.contracts.load_contract(service: str, operation_id: str) -> dict` and `verify_references(service: str) -> None`; catalog fields are defined in the index. Produce `async search_unified_audit(session: AsyncSession, actor: PrincipalContext, parameters: dict, now: datetime) -> list[dict]`. Consume canonical EvidenceVersion records and existing audit authorization; add `unified_audit_sessions.py` migration for snapshot membership, request fingerprint, owner, position, generation and expiry.

- [ ] **Step 1 — Add the named failing scenarios.** In test_search_session_resume, seed 1,101 ordered audit records across two tenants, request pages of 500, restart the API, exhaust the same session and compare canonical record IDs. In test_session_scope_and_filters, revoke rights between pages and reuse a session with changed filters/another actor; assert documented failures and no disclosure. Pin date parsing, filtered record counts, default/maximum ResultSize, ReturnLargeSet/preview behavior and Formatted from source rather than current generic Exchange result_size.

```python
assert len(collected_ids) == len(set(collected_ids)) == 1101
assert set(collected_ids) == expected_local_ids
assert exhausted_page == []
assert denied_page.status_code == 403
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/unit/test_cloud_contracts.py tests/integration/test_exchange_unified_audit.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Implement source acquisition/hash verification with per-service manifests and dependency closure; A1 ships actual audit search, not an empty contract framework. Persist and lock session position atomically; bind tenant, actor, filters and source generation, and reauthorize each page. Preserve AuditData as the documented serialized payload and source IDs across UAL interfaces. Include cleanup/reset/export rules, contract-derived expiry/result limits and tests for concurrent calls sharing a session. Record source differences explicitly.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add durable Exchange unified audit search"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task A2: Recipient resolution and mailbox delegation

**Files**

- Create: `src/mockazure/mail/recipients.py`, `src/mockazure/mail/delegation.py`, `tests/integration/test_exchange_recipients.py`.
- Modify: `src/mockazure/providers/exchange/routes.py`, `src/mockazure/providers/exchange/commands.py`, `src/mockazure/mail/mailboxes.py`, `src/mockazure/mail/permission_records.py`, `src/mockazure/mail/authorization.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async resolve_recipient(session: AsyncSession, tenant_id: UUID, identity: str) -> dict` and `async require_send_identity(session: AsyncSession, actor: PrincipalContext, mailbox_id: UUID, represented_id: UUID, mode: str) -> None` in delegation.py. Mode is `sendAs` or `sendOnBehalf`; E1 consumes this checker. Return canonical identifiers and qualified public recipient fields, never stored credentials.

- [ ] **Step 1 — Add the named failing scenarios.** test_recipient_identity_and_delegation resolves ID, SMTP and documented aliases; two recipients share a display name. Add/remove permitted delegation, query both permission command variants and mailbox GrantSendOnBehalfTo, revoke, then deny sending. A same-named recipient in a second tenant must remain inaccessible.

```python
assert recipient['ExternalDirectoryObjectId'] == str(expected_user_id)
assert ambiguous.status_code == expected_ambiguous_status
assert set(mailbox['GrantSendOnBehalfTo']) == expected_delegate_ids
assert revoked_send.status_code == 403
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_exchange_recipients.py tests/integration/test_exchange_compiled.py tests/integration/test_exchange_scope.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Pin Get-Recipient/Get-EXORecipient and compiled permission command wire contracts. Extend the real compiled transport paths observed from the pinned module, without replacing compiled commands. Separate descriptive permission imports from operational delegation grants; implement documented Add/Remove permission and Set-Mailbox delegation changes with atomic authorization and audit facts. Extend typed provisioning, migration and lifecycle routines.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: complete Exchange recipients and delegation"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task A3: Remote domains and audit/security configuration

**Files**

- Create: `src/mockazure/mail/security_configuration.py`, `src/mockazure/mail/security_configuration_models.py`, `tests/integration/test_exchange_security_configuration.py`.
- Modify: `src/mockazure/mail/configuration.py`, `src/mockazure/mail/collection_schemas.py`, `src/mockazure/providers/exchange/bootstrap.py`, `src/mockazure/providers/exchange/commands.py`, `src/mockazure/admin/mail_routes.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async invoke_security_configuration(session: AsyncSession, actor: PrincipalContext, command: str, parameters: dict, now: datetime) -> list[dict]`. Command allowlist comprises Get/New/Set/Remove variants for RemoteDomain, InboundConnector, OutboundConnector, AuthenticationPolicy, OrganizationRelationship and SharingPolicy where the official cloud command exists; Get/Set-AdminAuditLogConfig only for documented cloud-writable fields.

- [ ] **Step 1 — Add the named failing scenarios.** test_configuration_roundtrip reads defaults, creates and updates named configuration, restarts and rereads, deletes and verifies the documented absence. test_cloud_parameter_contract rejects on-premises-only inputs and conflicting parameter sets. test_audit_ingestion_configuration proves supported ingestion changes affect subsequent eligible publication, without deleting historical evidence.

```python
assert reread['Identity'] == created['Identity']
assert reread[selected_field] == changed_value
assert invalid.status_code == expected_parameter_status
assert historical_record_ids <= visible_record_ids
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_exchange_security_configuration.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Acquire each command page/parameter set and record output limitations. Add typed configuration storage, validation, identity uniqueness and command-specific serialization. Apply configuration only to supported simulated behavior: do not claim external connector/federation or basic-auth execution. Audit mutations transactionally. Implement typed admin fixtures and lifecycle export/reset including configuration authority distinctions.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add Exchange security configuration workflows"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task A4: Supported Exchange RBAC and module qualification

**Files**

- Create: `src/mockazure/mail/role_management.py`, `tests/integration/test_exchange_security_roles.py`, `integrations/powershell/Test-ExchangeWorkflows.ps1`.
- Modify: `src/mockazure/mail/authorization.py`, `src/mockazure/providers/exchange/bootstrap.py`, `src/mockazure/providers/exchange/commands.py`, `tools/qualify_mail_collectors.py`, `integrations/contracts/exchange-mail.json`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async invoke_role_management(session: AsyncSession, actor: PrincipalContext, command: str, parameters: dict, now: datetime) -> list[dict]`. Implement Get-RoleGroup/Get-RoleGroupMember, Add/Remove-RoleGroupMember and Get/New/Remove-ManagementRoleAssignment within supported built-in role scopes; use the same effective command check for module advertisement and invocation.

- [ ] **Step 1 — Add the named failing scenarios.** test_role_change_enforcement changes supported role membership/assignment, verifies a newly allowed command, removes it and denies the same command on an already established connection. Imported display-only role data must not grant a command. Qualification runs all A1-A3 workflows through the unchanged original module.

```python
assert permitted_command.succeeded is True
assert revoked_command.succeeded is False
assert oauth_grants_after == oauth_grants_before
assert display_only_grant.succeeded is False
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_exchange_security_roles.py tests/integration/test_exchange_unified_audit.py tests/integration/test_exchange_recipients.py tests/integration/test_exchange_security_configuration.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Pin role/group command and built-in management role references. Persist authoritative assignments separately from imported display metadata; check scoped current membership. Add command grants to module bootstrap only when actually implemented. Integrate A lifecycle tests and unchanged collectors; produce client result JSON through the common harness defined by G1, introduced incrementally here. Update COVERAGE with individual commands, supported parameters and configuration-only effects.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: enforce Exchange role changes and qualify workflows"`. Preserve unrelated work. Push/review according to the program's phase PR rules.
