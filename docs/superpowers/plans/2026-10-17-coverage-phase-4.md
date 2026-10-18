# Coverage phase 4 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.
> Native execution is selected. Steps use checkboxes; no implementation agents.

**Goal:** Manage supported authentication methods and enforce both method policies and
bounded Conditional Access conditions against actual local authentication state.
**Architecture:** Extend canonical security/method records, password verifiers and OAuth
sessions. Intune observations supply trusted device state; simulated method outcomes stay
explicit and separate from real hardware proof. No parallel policy engine.
**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker, PowerShell.
**Spec:** [Proposed program design](../specs/2026-10-17-coverage-expansion-design.md).
**Status:** Proposed; two PRs, 4A then 4B. No general security management expansion.

## Global Constraints

- Work only in MockAzure; never modify VTW or copy its secrets, generated files or untracked files.
- No real-tenant traffic; use official public Microsoft documentation and pinned schema sources.
- Native execution; commit cohesive changes, push reviewable PRs, do not auto-merge.
- Graph additions target v1.0 contracts; do not infer beta support from a shared router.
- No host trust, DNS or firewall changes; all generated service/content URLs stay local.
- Current COVERAGE.md describes shipped behavior; planned features remain separately labeled.
- Preserve existing data and accepted collector limitations; no silent destructive migration.

## Review Focus

- Privileged target versus ordinary target: administrator permission hierarchy is operation-specific (4A).
- Password reset retries and restart: a durable operation cannot reset a password twice (4A).
- Deleted/disabled method after challenge: a stale outcome cannot mint a new MFA-qualified token (4B).
- User-supplied device/MFA claims: headers and Graph payloads cannot establish trusted state (4B).
- Missing or unknown device compliance with OR/AND controls: use explicit decision logic (4B).

### Task 4A: public method management and cloud password reset

**Files:** Modify `src/mockazure/security/methods.py`, `registration.py`,
`src/mockazure/providers/graph/security/method_routes.py`,
`src/mockazure/identity/models.py`, `src/mockazure/oauth/sessions.py`,
`src/mockazure/jobs/dispatch.py`, `src/mockazure/authorization/catalog.py`;
create `src/mockazure/security/{method_mutations,password_reset}.py`,
`src/mockazure/oauth/password_change.py`, migration `method_management.py`,
`tests/unit/test_method_management_contracts.py`,
`tests/integration/test_method_management.py`, `test_password_reset.py`,
`integrations/powershell/Test-MethodManagement.ps1`.
Register the local password-change handler through the existing login router; inspect
its current file before wiring rather than adding a second login application.

**Interfaces:** `async mutate_method(session, context, user_id: UUID, kind: str,
method_id: str | None, action: str, payload: dict) -> dict | None` in method_mutations.py
uses an explicit action/kind matrix, never arbitrary attribute assignment.
`async start_password_reset(session, context, user_id: UUID,
new_password: str | None) -> ResetReceipt` in password_reset.py returns frozen
`ResetReceipt(operation_id: UUID, generated_password: str | None)`.
The HTTP adapter creates local Location and correct 202 body. Existing job dispatch
calls `async finish_password_reset(session, operation_id: UUID) -> None` idempotently.
Password verifiers use the existing hashing library; no recoverable plaintext job payload.

- [ ] Pin official v1.0 phone/email POST/PATCH/DELETE, Authenticator/FIDO2 DELETE, resetPassword
      and authentication/operations GET contracts. Record canonical fixed IDs, write fields,
      status codes, self-service scope differences and privileged-target role restrictions.
      Password reset is delegated-admin only and cannot target the caller. No app-only shortcut.
- [ ] Add failing `test_phone_email_lifecycle`, `test_authenticator_fido_delete`,
      `test_fixed_method_ids`, `test_self_and_privileged_target_matrix`,
      `test_no_secret_inventory_fields`, `test_password_reset_operation_restart`,
      `test_password_reset_forces_change` and `test_reset_rejects_app_only_and_self`.
      Assert method report changes, canonical User verifier changes, old password failure,
      no token until required password change, and no plaintext in audit/export/log output.
- [ ] Run `uv run pytest tests/unit/test_method_management_contracts.py tests/integration/test_method_management.py tests/integration/test_password_reset.py -q` red.
- [ ] Implement phone/email lifecycle and deletion of stored Authenticator/FIDO2 descriptors;
      update registration reports and native audit records transactionally. Deleting a
      descriptor removes eligibility, not the evidence of earlier sign-ins. Reject unsupported
      public enrollment for hardware/push methods and SMS-primary-sign-in actions explicitly.
- [ ] Implement durable cloud-only reset. Validate passwords under the documented local password
      profile; generated passwords returned once are cryptographically random. Persist only
      verifier material and operation status; job retry must not choose another password.
      Reject hybrid writeback. Add local authenticated password-change UI/handler with CSRF
      and one-use session state, invalidating outstanding reset credentials after completion.
- [ ] Test reset during code/device/refresh flows, concurrent requests, operation authorization
      and restart. Define session revocation behavior from the pinned reset contract and existing
      local security semantics; never let a reset-required user obtain tokens via refresh.
- [ ] Extend schema/source manifests, export redaction and lifecycle/upgrade tests. Qualify
      original SDK/PowerShell method management and browser reset/change flow locally. Run
      security/identity regressions, update coverage, commit/push and publish 4A.

### Task 4B: method policy and Conditional Access enforcement

**Files:** Create `src/mockazure/security/{method_policies,authentication_context}.py`,
`src/mockazure/providers/graph/security/method_policy_routes.py`,
`tests/unit/test_authentication_policy_contracts.py`,
`tests/integration/test_method_policy_enforcement.py`, `test_conditional_access_extended.py`,
`integrations/powershell/Test-AuthenticationPolicies.ps1`, migration `authentication_policy_state.py`.
Modify `src/mockazure/security/policies.py`, `evaluation.py`, `methods.py`,
`src/mockazure/oauth/sessions.py`, `src/mockazure/admin/session_routes.py`,
`src/mockazure/providers/graph/security/policy_routes.py`, permission/schema manifests
and existing login/MFA handlers at their current locations.

**Interfaces:** `async eligible_mfa_methods(session, tenant_id: UUID,
user_id: UUID) -> tuple[str, ...]` in method_policies.py returns registered method IDs
currently enabled for that user; group exclusions override inclusions. Supported simulated
methods are SMS and Microsoft Authenticator, not email or FIDO2 proof.
`TrustedAuthenticationContext` in authentication_context.py is a frozen dataclass with
`session_id: UUID`, `device_id: UUID | None`, `platform: str | None`,
`method_id: str | None`, `method_completed_at: datetime | None`.
`async load_authentication_context(session, session_id: UUID) -> TrustedAuthenticationContext`
reads server-maintained session state. Only the separate admin simulation control can bind
an existing local directory device/platform to a session; no untrusted bearer/header field
can perform that binding. Existing approved local MFA UI records a selected eligible method.

Keep `evaluate_policies(session, tenant, user, resource, context, now) -> PolicyDecision`
as the single entry point. Replace generic completed-MFA acceptance with the validated
session method outcome when method-policy enforcement is enabled. Use phase 3C
`device_observation` and directory role membership for new conditions. Intune observations
return unknowns explicitly; compliant-device succeeds only on a true observed compliance
value with a valid device binding. A deleted/disabled device is never compliant.

- [ ] Pin GET authenticationMethodsPolicy and GET collection/item plus PATCH SMS/Authenticator
      configurations and their supported target operations. Policy.ReadWrite.AuthenticationMethod
      and delegated Authentication Policy Administrator requirements are distinct from method
      management permissions. Root PATCH registration campaigns, passwordless mode and
      unimplemented advanced configuration flags remain unsupported in this first profile.
- [ ] Pin CA role and platform targeting plus compliantDevice grant schemas and licenses.
      Record unsupported filters, risk/session/strength/geolocation controls in validation.
      Platform is an explicitly simulated local device/session fact, not hardware attestation.
- [ ] Add failing policy tests for included/excluded groups, disabled methods, no enrolled eligible
      method, email rejection, target changes during challenge, role exclusions, platform matches,
      compliant/unknown/noncompliant device, AND/OR controls, report-only and disabled policy,
      app-only versus delegated authority, user-header spoofing, and refresh after policy change.
- [ ] Run `uv run pytest tests/unit/test_authentication_policy_contracts.py tests/integration/test_method_policy_enforcement.py tests/integration/test_conditional_access_extended.py -q` red.
- [ ] Implement method-policy read/configuration writes and real eligibility enforcement at
      challenge selection and token issuance. Persist explicit policy state and audit changes.
      On upgrades preserve the existing documented legacy simulation profile until the first
      successful method-policy write atomically enables enforcement. New tenants enforce
      method policies by default. Expose this status only through
      MockAzure admin/capabilities, never as an invented Graph schema property. New policy
      writes cannot silently create method registrations. Test this transition explicitly.
- [ ] Extend the existing CA evaluator and all issuance/refresh call sites. Recheck current
      method, policy, role, entitlement and device state consistently under the existing locking
      discipline. Add results to sign-in evidence; report-only records decisions without blocking.
- [ ] Extend lifecycle/export/upgrade and original SDK/PowerShell/browser qualification.
      Run existing identity/security/Intune regressions. Update coverage with exact supported
      policy fields and the explicit legacy-profile boundary; publish 4B after all CI gates.

## Whole-program completion gate (included in 4B)

- [ ] Create `tools/verify_coverage_program.py` to collect a deterministic two-tenant dataset:
      directory/Exchange -> mailbox MIME -> site/drive content/delta -> Teams messages ->
      Intune observations -> Activity feed, then method/policy mutation and permission denial.
      Use original clients where available; record exact source/client versions and output hashes.
- [ ] Extend `tools/scale_dataset.py` and add `tools/benchmark_coverage.py`: 100,000 message/item
      metadata records, a 256 MiB streamed object, eight concurrent clients, two API instances,
      restart and retry. Record resource usage/query plans on documented hardware; assert IDs,
      hashes, tenant isolation and preservation of acknowledged changes, not invented service SLAs.
- [ ] Add small deterministic qualification cases to CI; run `uv run ruff check .`, all unit and
      integration tests, `uv run python -m tools.verify_default`, and existing package/release
      checks. Validate fresh install plus upgrade from baseline with legacy statistics/policies.
- [ ] Reconcile every implemented operation and remaining gap in COVERAGE.md against the
      operation manifest, actual registered routes and successful wire evidence. Family-level
      collector mappings cannot substitute for verified request support.
- [ ] Review the whole branch against the design; resolve findings, rerun affected tests, commit,
      push and attach PR. Preserve existing upstream MES JSON limitation and all partial-client
      labels. Report passed gates and remaining limits without claiming universal compatibility.
