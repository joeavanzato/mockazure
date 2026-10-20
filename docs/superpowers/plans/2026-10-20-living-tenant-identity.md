# Identity Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution remains selected. Complete C1–C6 after Phase B.

**Goal:** Make onboarding, access changes, revocation, restoration and bounded Entra privilege activation behave operationally.

**Architecture:** Extend canonical identity/session/credential state and route all
effective-role checks through a common time-aware resolver. New lifecycle operations
emit the Phase B facts and use Graph v1.0 contracts.

**Tech Stack:** Existing Python/PostgreSQL/OAuth/Graph stack and cryptography library.

**Spec:** [Approved design](../specs/2026-10-20-living-tenant-design.md), sections 6–9.

## Global constraints

All [program constraints](2026-10-20-living-tenant.md#global-constraints) apply.
“Restoring an application must not implicitly restore its service principal or
resurrect revoked sessions.” “A stopped worker cannot leave an expired grant
effective.” No external invitation delivery/identity provider, group-based PIM,
Azure PIM, access reviews or entitlement-management subsystem.

## Review focus

- A successful revocation changes refresh/session behavior rather than only a field (C1).
- A deletion/restoration cannot restore revoked credentials or foreign dependencies (C2).
- Guest redemption cannot fetch or redirect to arbitrary remote destinations (C3).
- Key rollover cannot accept a proof signed with the newly submitted untrusted key (C4).
- Stale role caches and scattered direct role-table reads cannot bypass PIM expiry (C5).

## File boundaries

New `identity/lifecycle.py`, `identity/deleted.py`, `identity/invitations.py` and
`security/key_actions.py` have distinct transaction responsibilities.
`security/pim/` owns schedules/policies; `authorization/roles.py` owns effective
roles. Public routes remain in focused Graph modules. Population recipes compose
these operations without bypassing them.

## C1. Joiner/mover access changes and session revocation

**Files:** Create `src/mockazure/identity/lifecycle.py`,
`src/mockazure/providers/graph/identity_lifecycle_routes.py`,
`tests/integration/test_identity_lifecycle.py`,
`tests/integration/test_graph_session_revocation.py`.
Modify `identity/models.py`, `oauth/{sessions,authorization}.py`, directory
mutations and existing group/owner/licensing/grant route handlers as needed.
Add migration suffix `identity_session_revocation`.

**Interfaces:** `revoke_user_sessions(session, actor: PrincipalContext,
user_id: UUID, wall_now: datetime) -> dict` validates the operation contract,
updates revocation state and invalidates local refresh/session authority.
`require_live_user(session, tenant_id: UUID, user_id: UUID) -> User` is shared
by new lifecycle paths; reconcile with existing require_current_identity rather
than introducing competing enabled/deleted checks.

**Public set:** Graph v1.0 user create/PATCH/accountEnabled, manager reference
PUT/DELETE, supported assignLicense, group/member/owner changes, app-role and
delegated-grant changes; POST `/users/{id|UPN}/revokeSignInSessions` and
`/me/revokeSignInSessions`. Reuse existing paths where implemented. `/me` remains
delegated-only and per-operation permissions/privileged-target rules are enforced.

- [ ] **Tests:** `test_joiner_can_use_only_assigned_services`,
  `test_mover_loses_removed_access`,
  `test_leaver_cannot_get_new_or_refreshed_tokens`,
  `test_revoke_sessions_advances_valid_from`,
  `test_application_cannot_revoke_me`,
  `test_external_guest_home_sessions_are_not_claimed_revoked`.
  Test real token acquisition/refresh and a protected operation after each change.
  ```python
  assert permitted_resource_response.status_code == 200
  assert removed_access_response.status_code == 403
  assert refresh_after_revocation["error"] == "invalid_grant"
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/integration/test_identity_lifecycle.py tests/integration/test_graph_session_revocation.py -q`.
- [ ] **Implement:** Preserve credential-version and current identity enforcement;
  invalidate the correct subject's refresh handles/browser authority atomically.
  Use wall time for revocation and explicitly document existing access-token
  behavior; do not invent a claim that every issued bearer is immediately invalid.
  Account disablement is distinct from merely revoking sessions.
- [ ] **Integrate evidence:** Emit sourced directory/session facts, with actor and
  target but no tokens. Add needed permissions to the sourced catalog, not only
  to seeded privileged clients.
- [ ] **Verify:** New tests and existing identity authorization/session/token suites
  pass; manager cycles/foreign references and privileged target denials are covered.
- [ ] **Commit:** `feat: add operational user lifecycle and session revocation`.

## C2. Deleted directory objects and safe restoration

**Files:** Create `src/mockazure/identity/{deleted,deleted_models}.py`,
`src/mockazure/providers/graph/deleted_routes.py`,
`tests/integration/test_directory_recycle_bin.py`,
`tests/integration/test_identity_lifecycle_migration.py`.
Modify directory/application/service-principal deletion, identity lookup and delta
paths. Add migration suffix `directory_recycle_bin`.

**Interfaces:** `deleted_item(session, actor, object_id: UUID) -> dict`;
`restore_object(session, actor, object_id: UUID, options: dict,
wall_now: datetime) -> dict`; `purge_object(session, actor, object_id: UUID,
wall_now: datetime) -> None`. Expose list/type-cast/item GET, POST restore and
permanent DELETE only on sourced Graph variants.

**Type scope:** Users, applications and service principals. Group restore is
enabled only for types both permitted by the pinned Microsoft contract and actually
implemented locally; ordinary static security groups must not acquire unsupported
restore semantics. Preserve this explicit limitation in coverage.

- [ ] **Tests:** `test_deleted_user_is_hidden_from_live_queries_and_auth`,
  `test_restore_conflict_is_atomic`,
  `test_application_restore_does_not_restore_service_principal`,
  `test_restore_does_not_revive_revoked_session_or_credential`,
  `test_retention_expires_without_worker_permission_leak`,
  `test_unsupported_group_type_is_not_restorable`,
  `test_upgrade_preserves_existing_live_and_deleted_identity_state`.
  ```python
  assert restored["id"] == deleted_user_id
  assert restored_service_principal is None
  assert refresh_after_restore["error"] == "invalid_grant"
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/integration/test_directory_recycle_bin.py tests/integration/test_identity_lifecycle_migration.py -q`.
- [ ] **Implement:** Enforce documented 30-day deleted-object availability using
  wall time; preserve historical evidence and ID references. Handle documented user
  UPN/proxy-conflict restore options and atomic uniqueness. Restore eligible owned
  relationships only according to an explicit type dependency matrix; reject
  conflicting surviving relationships instead of retargeting them.
- [ ] **Migrate:** Existing already-deleted rows are represented only where enough
  retained data exists for a safe restore. Do not invent missing passwords,
  credentials, consent or memberships. Permanent deletion keeps safe historical
  references/tombstones and no operational access.
- [ ] **Verify:** New suites and directory paging/delta/authorization tests pass;
  response type/permissions match sourced v1.0 contracts.
- [ ] **Commit:** `feat: add safe directory deletion and restoration workflows`.

## C3. Local guest invitations and redemption

**Files:** Create `src/mockazure/identity/{invitations,invitation_models}.py`,
`src/mockazure/providers/graph/invitation_routes.py`,
`src/mockazure/admin/guest_routes.py`,
`tests/integration/test_guest_invitations.py`.
Modify user serialization and catalog/console relationship views.
Add migration suffix `guest_invitations`.

**Interfaces:** `create_invitation(session, actor, body: dict,
wall_now: datetime) -> dict`; `redeem_local_invitation(session, tenant_id: UUID,
invitation_id: UUID, wall_now: datetime) -> dict`.
Public POST `/v1.0/invitations`; authenticated local POST
`/_mock/tenants/{tenant}/guests/{invitation}/redeem`.
The local route is explicitly not a Microsoft Graph redemption endpoint.

- [ ] **Tests:** `test_invitation_pending_then_local_acceptance`,
  `test_duplicate_invitation_follows_declared_existing_user_contract`,
  `test_guest_membership_controls_resource_access`,
  `test_redemption_is_tenant_bound_and_replay_safe`,
  `test_invitation_never_sends_email_or_fetches_redirect`,
  `test_external_session_limit_is_preserved`.
  ```python
  assert guest_before["externalUserState"] == "PendingAcceptance"
  assert guest_after["externalUserState"] == "Accepted"
  assert guest_grants_after == guest_grants_before
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/integration/test_guest_invitations.py -q`.
- [ ] **Implement:** Source invitation fields, userType/externalUserState and
  permission/role requirements. Return only locally generated redemption URLs.
  Restrict actionable redirects to the configured local allowlist. Simulate
  invitation delivery as local mailbox/content where supported; never contact an
  external address. Explicitly record which delivery fields are simulated.
- [ ] **Integrate:** Guest actors receive the same current group/site/Teams access
  checks as other local identities. Redemption cannot implicitly add grants.
  Record invitation/acceptance evidence and expose status in the service catalog.
- [ ] **Verify:** New suite plus session and workload authorization regressions pass.
- [ ] **Commit:** `feat: support local guest invitation lifecycle`.

## C4. App secret and certificate rollover

**Files:** Create `src/mockazure/security/key_actions.py`,
`tests/unit/test_key_rollover_proof.py`,
`tests/integration/test_graph_credential_rollover.py`.
Modify `security/credentials.py`, existing application/service-principal action
routes, credential models only if required, and operation/permission contracts.

**Interfaces:** `verify_key_proof(proof: str, *, existing_keys: list,
owner_id: UUID, audience: str, wall_now: datetime) -> None`;
`add_key(session, actor, owner_id: UUID, body: dict, wall_now: datetime) -> dict`;
`remove_key(session, actor, owner_id: UUID, body: dict,
wall_now: datetime) -> None`. Use the exact sourced proof claims, signing-key
ownership, token lifetime and algorithm rules; no signature bypass.

**Public set:** `/v1.0/applications/{id}/{addKey,removeKey}` and
`/v1.0/servicePrincipals/{id}/{addKey,removeKey}`. Exercise existing
addPassword/removePassword paths and bootstrap certificate PATCH separately.

- [ ] **Tests:** `test_proof_requires_existing_eligible_key`,
  `test_wrong_owner_audience_signature_and_time_fail`,
  `test_new_key_cannot_authorize_its_own_addition`,
  `test_new_credential_acquires_token_and_removed_one_fails`,
  `test_secret_is_disclosed_once_and_not_audited`,
  `test_managed_identity_backing_credentials_stay_protected`.
  ```python
  assert added_key_id in listed_key_ids
  assert new_key_token["token_type"] == "Bearer"
  assert removed_key_token["error"] == "invalid_client"
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_key_rollover_proof.py tests/integration/test_graph_credential_rollover.py -q`.
- [ ] **Implement:** Reuse cryptography and existing token credential validation;
  lock owner credential state while validating proof and changing the set.
  Handle expired/missing bootstrap key cases explicitly according to the operation
  documentation. Do not silently redirect addKey into an unrestricted PATCH.
- [ ] **Integrate:** Add public recipes and redacted native evidence. Client private
  keys remain in local protected test/runtime storage, never generated profile exports.
- [ ] **Verify:** New suites plus existing app-only and credential tests pass.
- [ ] **Commit:** `feat: implement proof-validated Graph key rollover`.

## C5. Time-bound Entra PIM and effective-role enforcement

**Files:** Create `src/mockazure/security/pim/{models,contracts,policies,requests,schedules}.py`,
`src/mockazure/authorization/roles.py`,
`src/mockazure/providers/graph/security/pim_routes.py`,
`tests/unit/test_pim_contracts.py`,
`tests/integration/test_pim_lifecycle.py`,
`tests/integration/test_pim_authorization_consumers.py`.
Modify role-aware consumers in authorization, Graph licensing/permissions/evidence,
OAuth admin consent, authentication methods/policy evaluation/grants/registration,
and existing role views. Add migration suffix `pim_schedules`.

**Interfaces:** `submit_request(session, actor, kind: Literal["eligibility","assignment"],
body: dict, wall_now: datetime) -> dict`;
`cancel_request(session, actor, kind, request_id: UUID, wall_now) -> None`;
`effective_roles(session, tenant_id: UUID, principal_id: UUID,
wall_now: datetime) -> frozenset[str]`.
The resolver unions valid direct roles with valid active schedules, never eligibility.
It checks principal live state and current schedule bounds on each authorization path.
`principal_id` is the canonical directory object ID; service-principal callers must
resolve their client/app ID to that object before calling it.

**Public set:** Under `/v1.0/roleManagement/directory`, collection/item reads and
request POST/cancel for roleEligibilityScheduleRequests and
roleAssignmentScheduleRequests; reads of their schedules and instances; documented
filterByCurrentUser variants. Under `/v1.0/policies`, reads of
roleManagementPolicies, rules and roleManagementPolicyAssignments plus supported
rule PATCH. Exact operation variants are pinned before implementation.

**Local profile:** Built-in roles at tenant scope `/`; user eligibility and
delegated self-activation. Admin assignment/removal and supported scheduling for
eligible principals follow the sourced contract. Initial actions:
adminAssign/adminRemove/adminUpdate, selfActivate/selfDeactivate and pending request
cancel where valid. Other enum actions fail explicitly until implemented.
Activation duration follows the stored expiration rule, with a maximum eight hours.
Justification and trusted MFA requirements are enforced. Approval-required requests
are explicitly unsupported and cannot produce an active grant; no fake approval.

- [ ] **Tests:** `test_eligibility_is_not_authority`,
  `test_activation_requires_eligible_self_and_mfa`,
  `test_duration_and_justification_policy`,
  `test_future_grant_is_not_early_active`,
  `test_expired_grant_denies_with_worker_stopped`,
  `test_direct_grant_survives_pim_expiry`,
  `test_approval_required_is_not_autoapproved`,
  `test_cancelled_request_never_activates`,
  `test_all_role_consumers_use_current_effective_roles`.
  ```python
  assert eligible_only_response.status_code == 403
  assert active_response.status_code == 200
  assert expired_response_with_worker_stopped.status_code == 403
  assert direct_role_name in effective_after_pim_expiry
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_pim_contracts.py tests/integration/test_pim_lifecycle.py tests/integration/test_pim_authorization_consumers.py -q`.
- [ ] **Implement:** Separate request state, schedules, instances and effective
  authorization. Lock principal/role scope during changes; validate license,
  permissions, scope and current actor authority before mutation. Add policy
  expiration/enablement rules needed by the profile; reject unsupported rules.
  Capture native directory audits. Background cleanup is housekeeping only.
- [ ] **Reconcile consumers:** Replace authorization reads of direct DirectoryRole
  tables with the shared resolver or its batch-query equivalent. Audit all uses
  with `rg -n "DirectoryRole|ServicePrincipalRole" src/mockazure`, including mail
  and administration paths outside the Graph router. Inventory views
  distinguish direct/eligible/active rather than turning eligibility into legacy
  active rows. Test consent, licensing, methods, evidence reads and protected writes.
  Test immediate deactivation and account disablement independently from expiry.
- [ ] **Verify:** New suites and identity/security/policy regressions pass; original
  Graph PowerShell performs eligibility → activation → permitted action →
  deactivation → denied action. Time-controlled tests use a trusted wall-clock fixture,
  never a client-controlled evidence timestamp.
- [ ] **Commit:** `feat: add bounded Entra PIM with effective authorization`.

## C6. Lifecycle profiles, console visibility and original-client qualification

**Files:** Create `src/mockazure/population/generators/business_identity.py`,
`src/mockazure/profiles/data/data/business-identity-lifecycle.json`,
`src/mockazure/profiles/data/data/business-privileged-access.json`,
`tests/acceptance/test_living_identity_workflows.py`,
`docs/identity-lifecycle.md`, `docs/qualification/living-identity.md`.
Modify registry, service adapters/catalog, console resources, verifier and COVERAGE.md.

**Interfaces:** Existing `generate`/`ServiceAdapter` contracts; A6 verifier gains
`--phase identity`. Use new namespaces for destructive lifecycle exercises and
designate disposable subjects explicitly in previews.

- [ ] **Tests:** `test_joiner_mover_leaver_end_to_end`,
  `test_guest_invite_accept_collect`,
  `test_delete_restore_collect_preserves_audit`,
  `test_rotation_changes_real_token_acquisition`,
  `test_pim_changes_api_access`,
  `test_azure_assignment_removal_changes_data_plane_access`.
  Assert both allowed and denied requests, not only successful admin responses.
  ```python
  assert report["failed_cases"] == []
  assert report["unexpected_authorizations"] == []
  assert audit_target_ids >= lifecycle_subject_ids
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/acceptance/test_living_identity_workflows.py -q`.
- [ ] **Implement recipes/UI:** Stack lifecycle layers on the business baseline.
  Bind actual returned IDs, show operation mode/destructive effects, display
  deleted/guest/eligible/active states and reference time separately from security time.
  Preserve original generated content and audit IDs for historical collection.
- [ ] **Qualify:** `python -m tools.verify_living_tenant --config PATH --phase identity --report work/living-identity.json`
  uses original Graph clients and Azure SDKs for the existing RBAC scope workflow.
  Include a native-database upgrade test before qualification.
- [ ] **Verify/document:** Acceptance passes; update URI/method/permission/limit
  coverage, migration guidance and exact session/PIM limitations.
- [ ] **Commit:** `test: qualify connected identity and access lifecycle`.
