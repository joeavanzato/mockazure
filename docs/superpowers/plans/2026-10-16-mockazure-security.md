# M3C Security Administration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (Native selected) task-by-task. Steps use checkbox syntax.

**Goal:** Deliver real stateful Graph security administration and a bounded enforced Conditional Access profile.
**Architecture:** Extend normalized identity/grant/role tables, typed supplementary security resources and caller-bound snapshot paging; all mutations share transactional audit evidence and current authorization. Reuse real OAuth and SDK paths.
**Tech Stack:** Python>=3.12,existing FastAPI/SQLAlchemy2/psycopg3/Alembic/PostgreSQL18,cryptography/PyJWT,HTTPX,pytest; existing MSAL1.39.0/GraphPython1.64.0/AzureIdentity1.26.0 and PowerShell pins.
**Spec:** ../specs/2026-10-16-mockazure-security-design.md
**Status:** Decision approved under explicit overnight delegation,2026-10-16; Native implementation authorized.
**Base:** main5cdcf5a; isolated D:/projects/mockazure/.worktrees/security.

## Global Constraints
- Work only in owned D:/projects/mockazure paths; never VTW or real tenants, host trust/DNS/firewall changes.
- Preserve all earlier runtime instances. Use fresh own test credential, port55438 and unused subnets10.231.80.0/24,10.231.81.0/24 for test-only tmpfs PostgreSQL; functional runtime must use persistent storage.
- Use real official schemas and permission IDs, no invented Microsoft contracts. Unknown operations/query features fail explicitly.
- One test suite at a time per disposable test DB; focused RED/GREEN per task, full suite after final review fixes.
- No generated or copied secrets in Git/output logs; credentials displayed only in private local client files or one-time API response.
- Permissions/roles/licences/cross-tenant checks apply to every route and continuation.
- Preserve immutable facts and worker fences; audit changes cannot succeed separately from their authoritative mutation.
- Native inline implementation, one final independent reviewer, no implementation agents. User's overnight instruction replaces routine approval pauses; record decisions and proceed.
- Frequent coherent commits and pushed PRs; no automatic merges. Continue M4,M5,M6 after verified predecessors.
- Existing MES JSON exception is narrow and does not waive new security/Exchange/ARM gates.

## Review Focus
1. Graph objectId/appId/SP IDs and tenant references must never alias into another identity; Tasks2-4.
2. Credential,grant,role or policy changes during pending OAuth/refresh must have declared effective timing; Tasks3-5,8.
3. Owner/lesser-admin privileges must not escalate to tenant-wide grant/role/credential control; Tasks3-5.
4. Unimplemented policy conditions/report-only must not silently create misleading successful enforcement; Tasks7-8.
5. Secret values must not reach snapshots,audit,exports/logs or unrelated callers; Tasks2,3,6.

## Task 1: Pin structural schemas and permission identifiers

**Files:** tools/extract_graph_schema.py; src/mockazure/providers/graph/schemas/{v1.0,beta}.json; src/mockazure/security/catalog.py; src/mockazure/security/data/permissions.json; tests/unit/test_security_reference.py.
**Interfaces:** Existing pinned CSDL extractor and supported permission names. Produces permission_id(name:str,kind:str)->UUID, permission_name(id:UUID,kind:str)->str, resource_app_id(resource:str)->UUID; only official supported IDs.

- [ ] Write focused failing tests: test_security_reference_types validates application,SP,device,grants,roles,CA and concrete methods against extracted CSDL; wrong primitive/nullability/unknown enum fails. test_permission_ids distinguishes application/delegated GUIDs and rejects unknown IDs; provenance hash and repository revision required.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/unit/test_security_reference.py -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.
## Task 2: Persist canonical objects and stable security collection snapshots

**Files:** src/mockazure/security/{models,repository,paging}.py; src/mockazure/identity/models.py; src/mockazure/db/alembic/versions/017_security_state.py; src/mockazure/providers/graph/security/{routes,queries,serialization}.py; tests/integration/test_security_resources.py.
**Interfaces:** Produces application_object(session,tenant,id_or_appid)->Application, public_security_object(session,tenant,kind,id)->dict, security_collection(request,session,actor,kind,query)->dict. Application.object_id UUID distinct from client_id, profile JSONB; SP.profile JSONB; typed supplementary resources and tenant-scoped owner edges. SecuritySnapshot header plus chunked item rows bind caller/query/expiry. No secret fields in stored snapshots.

- [ ] Write focused failing tests: test_legacy_application_upgrade_preserves_oauth: migration preserves existing client IDs/credentials and stable distinct Graph IDs. test_security_collection_shapes reads apps/SPs/devices and resource SP catalog, selects/filters/order/top/page; edits between pages do not duplicate/drop snapshot IDs. test_security_cross_tenant_cursor_and_reference rejects foreign tenant/caller/rebound query, expired snapshot and unsupported query. New role/grant permission catalogs registered before routes.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_security_resources.py -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.
## Task 3: Implement object, ownership and credential state transitions with audit facts

**Files:** src/mockazure/security/{mutations,credentials,audit}.py; src/mockazure/providers/graph/security/routes.py; src/mockazure/identity/{repository,models}.py; src/mockazure/oauth/client_auth.py; tests/integration/test_security_mutations.py.
**Interfaces:** Produces mutate_security(session,actor,kind,operation,id,body,now)->dict|None; record_security_audit(session,actor,operation,target,before,after,now)->SourceRef, using existing evidence outbox. Mutations take shared tenant security lock, including admin identity bridges. Owners tenant-local users/SPs; device registeredOwners. Credentials valid start/end and revoked_at, addPassword returns secretText once.

- [ ] Write focused failing tests: test_application_password_oauth_roundtrip creates app,adds secret,requests actual local token,revokes secret and proves next token denied; old issued access token semantics unchanged. test_certificate_credentials verifies public certificate upload and signed assertion; no private key/hash/secret in reads/snapshot/export/audit. test_owner_scoped_write_and_cross_tenant protects OwnedBy/roles and owner removal. test_security_mutation_audit_atomic verifies failure rollback and immutable audit. Unsupported writable fields fail explicitly.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_security_mutations.py -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.
## Task 4: Expose effective delegated grants and application role assignments

**Files:** src/mockazure/security/grants.py; src/mockazure/providers/graph/security/grant_routes.py; src/mockazure/authorization/catalog.py; tests/integration/test_security_grants.py.
**Interfaces:** Consumes official permission GUID mapping and canonical SPs. Produces grant_collection(session,actor,kind,principal)->list[dict], mutate_grant(session,actor,kind,id,body,now)->dict|None; updates identity.Grant rows used by OAuth. Application assignments expose one stable ID per granted appRole; delegated scopes support Principal/AllPrincipals.

- [ ] Write focused failing tests: test_graph_grant_token_effect grants through actual Graph IDs and proves new token claims/API authorization; revoke prevents new grants/refresh escalation. test_existing_admin_grants_visible proves one effective state, duplicate/cross-tenant/unknownGUID/mismatchedresource rejected. test_grant_privilege_checks distinguishes delegated/application permissions and privileged role. Audit is atomic and free of credentials.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_security_grants.py -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.
## Task 5: Expose and enforce stable directory-role assignments

**Files:** src/mockazure/security/roles.py; src/mockazure/providers/graph/security/role_routes.py; src/mockazure/authorization/catalog.py; tests/integration/test_security_roles.py.
**Interfaces:** Produces role_definitions()->list[dict], role_assignments(session,tenant)->list[dict], mutate_role_assignment(session,actor,id,body,now)->dict|None. Official builtin template IDs; normalized existing user/SP roles; deterministic effective assignment IDs; supports directoryScopeId='/' only. Reject unsupported custom roles/PIM and appScopeId.

- [ ] Write focused failing tests: test_role_assignment_changes_effective_access proves delegated denied->assigned->allowed->removed->denied on real protected operation. test_privileged_role_assignment_requires_privileged_admin denies lesser-role escalation and cross-tenant targets. test_role_graph_and_legacy_views_match validates roleDefinitions,roleAssignments,directoryRoles/members. Role mutation emits audited facts.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_security_roles.py -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.
## Task 6: Expose honest authentication-method inventory

**Files:** src/mockazure/security/methods.py; src/mockazure/admin/security_routes.py; src/mockazure/providers/graph/security/method_routes.py; tests/integration/test_security_methods.py.
**Interfaces:** Produces stored_methods(session,tenant,user,kind=None)->list[dict]; authenticated control-plane enrollment/inspection schema for lab method descriptors. Password method reflects actual password-backed user, other methods require persisted enrollment. Graph only reads the declared collection/item/concrete paths in M3C.

- [ ] Write focused failing tests: test_methods_inventory_and_masking verifies v1 concrete @odata.type, enrollment persistence and appropriate role/permission/self restrictions. test_no_secret_disclosure rejects seed/private-key/password import fields and never emits credentials. test_foreign_or_deleted_user_methods cannot expose another tenant or fabricate active enrollment.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_security_methods.py -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.
## Task 7: Persist and validate Conditional Access policies and IP named locations

**Files:** src/mockazure/security/policies.py; src/mockazure/providers/graph/security/policy_routes.py; tests/unit/test_policy_contract.py; tests/integration/test_security_policies.py.
**Interfaces:** Produces validate_policy(body:dict,*,partial=False)->dict, validate_named_location(body:dict)->dict; CRUD reuses security revisions/snapshots and audit. Official states enabled/disabled/enabledForReportingButNotEnforced. Declared users/groups/resources/client categories/IP locations, block/mfa only; reject unsupported enforced controls/conditions and invalid CIDRs.

- [ ] Write focused failing tests: test_policy_schema_and_supported_matrix rejects silently ignored conditions, invalid state/control/operator combinations and secret fields. test_policy_permissions_and_p1 enforces permission conjunctions, delegated role and licence. test_policy_crud_persistence_audit validates references, deletion, stable paging and restart state. Exclusion precedence and disabled/report-only semantics specified in evaluator task.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/unit/test_policy_contract.py tests/integration/test_security_policies.py -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.
## Task 8: Enforce policies across delegated OAuth and publish decision evidence

**Files:** src/mockazure/security/evaluation.py; src/mockazure/oauth/{authorization,sessions,device}.py; src/mockazure/evidence/{authentication,projectors,schemas}.py; tests/integration/test_policy_enforcement.py.
**Interfaces:** Produces evaluate_policies(session,tenant,user,resource,context,now)->PolicyDecision(blocked:bool,requires_mfa:bool,status:str,results:tuple[dict,...]); context has actual peer IP,known client category,mfa outcome. Use group relationships; no untrusted forwarding headers. Gate before authorization/code/device approval and on code/refresh redemption. Capture decision facts with existing evidence transaction, never infer from later policy state.

- [ ] Write focused failing tests: test_block_mfa_report_only authenticates actual local form/PKCE flows: block denies, enforced MFA rejects skip and accepts complete, report-only never blocks. test_policy_target_exclusions_and_ipv4_ipv6 covers users/groups/resources/locations exclusion precedence. test_policy_change_before_redeem_refresh_and_device cannot bypass a new block/MFA. test_ca_evidence_and_denied_disclosure verifies exact signIn status/policy decisions, immutable post-change facts and permission-based omission.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_policy_enforcement.py -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.
## Task 9: Qualify real clients on an isolated native/container M3C runtime

**Files:** integrations/python/graph_security.py; integrations/powershell/Test-GraphSecurity.ps1; tools/{prepare_security,verify_security}.py; compose.m3c.yaml; compose.clients.m3c.yaml; deploy/haproxy-m3c.cfg; tests/unit/test_security_report.py; tests/acceptance/test_security_distribution.py.
**Interfaces:** Qualification report binds source/manifest/lock/wheel/image/client/source pins and exact checks. New .runtime/m3c only; determine unused ports/subnets, explicit CA. Use real MSAL/Graph Python and pinned Graph PowerShell commands; existing network isolation and fail-closed adapter patterns. Permission/catalog/schema choices documented in integrations/contracts/graph-security.md before use.

- [ ] Write focused failing tests: test_security_report_rejects_missing_or_stale checks verifies no process-exit-only success. Actual Python and PowerShell app/delegated CRUD/grant/role/credential/method/CA reads and denials; real local OAuth block/MFA/report-only, two API/workers with PostgreSQL restart and snapshot continuation, original available security collector closures where required by their source. Build clean wheel, fingerprint image, run verifier then acceptance. No public tenant fallback.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/unit/test_security_report.py tests/acceptance/test_security_distribution.py -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.
## Task 10: Run full regression, one review, and publish M3C

**Files:** README.md; docs/{capabilities,compatibility,security-execution,security-decisions,overnight-execution}.md; this plan.
**Interfaces:** Consumes Tasks1-9 verified runtime/interfaces. Produce committed measured support matrix and PR; next M4 plan consumes qualified M3C branch without waiting for merge. One fresh reviewer per Native rules; reproduce/fix important findings in one pass and requalify changed runtime.

- [ ] Write focused failing tests: Run all tests/unit tests/integration,ruff,package provenance,git diff --check and fresh distribution acceptance. Full existing M1/M2/M3A/M3B regression remains required. Publish accurate measured evidence and decisions; no blanket compatibility claim. Commit/push/attach PR, preserve runtime/worktree, advance overnight queue to M4; do not stop at this milestone.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q --tb=short` with this task's own work/ basetemp and log; expected RED for the absent behavior before implementation.
- [ ] Implement the named interfaces and documented operation-specific contracts; preserve source provenance. Add negative cases discovered while implementing before fixing them.
- [ ] Run the same focused command; expected PASS with exact assertions above. Inspect output and run affected existing regressions.
- [ ] Run `.venv/Scripts/ruff.exe check . --no-cache` and `git diff --check`; expected exit0. Commit only task-owned coherent changes and record test evidence.

## Coverage and handoff
Resources/IDs/paging:1-3. Credentials/effective access:3-5. Methods:6. Policies:7-8. Audit and isolation:2-9. SDK/runtime/release evidence:9-10. Full parent M3C scope remains required; never relabel a partial read-only surface as completed policy administration.
Later milestones get their own written spec/plan after this milestone's verified interfaces; overnight execution continues through M6 without routine approval pauses.
