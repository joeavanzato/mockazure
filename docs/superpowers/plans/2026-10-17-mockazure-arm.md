# MockAzure M5 Azure Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Native execution is already selected. No implementation agents; one final reviewer only.

**Goal:** Persist and authorize real local ARM management interactions and export exact Activity Log evidence through original clients.
**Architecture:** Canonical tenant/subscription/resource state and inherited RBAC back ARM routes. Transactions append immutable evidence; the shared fenced worker handles projection and group deletion. Explicit API contracts bound provider support.
**Tech Stack:** Python3.12+, FastAPI, SQLAlchemy2, PostgreSQL18, Alembic, existing durable jobs and evidence pipeline.
**Spec:** docs/superpowers/specs/2026-10-17-mockazure-arm-design.md

## Global Constraints

- Own D:/projects/mockazure/.worktrees/arm, feat/azure-management; preserve predecessors.
- No VTW, real tenants, host trust/DNS/firewall, secret copies, merges or registry publishing.
- Native inline execution; one fresh final reviewer and one consolidated fix pass.
- Pin official Azure REST Swagger revision/SHA256 and client archives before implementation/use.
- Versions: discovery2020-01-01/2021-01-01; resources2021-04-01; authorization2022-04-01; Activity Logs2015-04-01.
- PostgreSQL ownership/FKs, current identities/RBAC at every page/poll; no public fallback.
- Page cap100/top1..1000, cursor1hour; maximum50 tags and1000 imported inventory rows.
- Own ports55443,55444,36443–36446 and networks10.231.96–99/24 after availability checks.
- M4 collector failures remain blockers to combined release acceptance; no new waiver.

## Review Focus

1. Encoded/case-varied/nested ARM IDs and prefix-confusable scopes must not cross grants or tenants (Tasks1–3).
2. Concurrent tag edits and group deletion versus imports/assignment writes must serialize without orphan state or false successful evidence (Tasks2–4).
3. Token claims, SP object/client IDs, transitive group cycles and permission union must not cause privilege escalation or survive revocation (Tasks2,4).
4. Deletion jobs and Activity Log pages across lease loss, reset, retention and process restart must not silently complete stale work or lose tied events (Tasks3,5).
5. Original Az/MES version/filter/date shapes, empty/malformed exports and redirected continuations must fail explicitly instead of producing convincing false evidence (Tasks1,6).

## File map

providers/arm/{contracts,scopes,authorization,inventory,inventory_routes,rbac,rbac_routes,
paging,errors,activity,activity_routes,activity_query,operations}.py separate wire,
authorization, storage and query responsibilities. Existing models.py/probes.py and
admin/arm_routes.py evolve in place. Evidence projection integrates through
arm/activity.py, not a second event store. jobs/dispatch.py registers typed deletion.
Tools/integrations own real-client qualification, not production provider behavior.

### Task 1: Pin contracts and canonical scope/query boundaries

**Files:** Create providers/arm/{contracts,scopes,errors}.py and data/{references,operations,roles}.json; tools/acquire_arm_contracts.py; tests/unit/test_arm_contracts.py; docs/arm-contracts.md; compose.test.m5.yaml.
**Interfaces:** parse_scope(value:str)->ArmScope(tenant-independent canonical key,subscription_id,group,resource_parts,display_id,parent_keys); operation_contract(name,version)->dict; validate_arm_payload(type_name,payload,partial=False)->dict. ArmError(status,code,message,target=None) maps to ARM error body.

- [ ] Write failing tests for exact API-version allowlists and official schema fields; scope case equivalence, segment inheritance, encoded separators/traversal rejection, nested resource pairs and malformed GUIDs. Assert scope(a) does not contain scope(ab).
- [ ] Run pytest tests/unit/test_arm_contracts.py -q; expect missing contracts/scope behavior RED, correct fixture errors first.
- [ ] Acquire exact official Azure/azure-rest-api-specs references and dependent schemas with revision/hash manifest; preserve licence/provenance. Implement schema validators, immutable role definitions and strict scope parser. Pin filter/date/action references before consumers use them.
- [ ] Run focused tests, Ruff and reference hash verification; expect PASS.
- [ ] Commit contracts, scope/error boundary and M5-only test runtime configuration.

### Task 2: Persist inventory and inherited RBAC with legacy compatibility

**Files:** Modify providers/arm/models.py,admin/arm_routes.py,identity principal lookup; create providers/arm/{authorization,inventory}.py, migration030; tests/integration/test_arm_state.py and test_arm_authorization.py.
**Interfaces:** resolve_scope(session,tenant,ArmScope)->owned entity; require_action(session,actor,scope,action)->None or ArmError; readable_scopes(session,actor,action)->SQL predicate; inventory_view(row,expand=())->dict; admin seed inputs use typed tenant ownership. Existing ReaderAssignment endpoints retain behavior through one canonical assignment table.

- [ ] Test migration of legacy Reader grants/revocation, subscription/group/resource inheritance, tenant FKs, case uniqueness, local SP object versus client ID, disabled/deleted principals and transitive security groups/cycles.
- [ ] Test Reader denied writes, Contributor denied RBAC changes, Owner allowed, User Access Administrator denied general writes; union permits another role's granted action despite Contributor notActions. Assert no Entra/Graph implicit access.
- [ ] Run tests/integration/test_arm_state.py tests/integration/test_arm_authorization.py for behavioral RED.
- [ ] Add canonical groups/resources/assignments, indexes and admin seed/bootstrap; implement current principal checks and SQL-access filters. Serialize group lifecycle, reject import into Deleting groups and enforce max1000/secret field validation.
- [ ] Run focused plus tests/integration/test_identity_arm.py; verify legacy contract and isolation, then commit.

### Task 3: ARM discovery, inventory, groups and tags

**Files:** Modify providers/arm/probes.py and app router registration; create providers/arm/{inventory_routes,paging,operations}.py, migrations as needed; tests/integration/test_arm_inventory.py,test_arm_mutations.py,test_arm_delete_jobs.py.
**Interfaces:** routes call Task2 require_action/resolve_scope; append_arm_fact(session,actor,operation,scope,before,after,now,correlation_id)->SourceRef; process_arm_delete(database,JobClaim,now)->JobResult; opaque nextLink uses existing Continuation storage with explicit binding.

- [ ] Test exact tenants/subscriptions/group/inventory shapes, declared versions/filter/top/expand/tag omission, unsupported queries and caller-bound pages with current revocation.
- [ ] Test RG PUT/PATCH location constraints, concurrent tag Merge/Replace/Delete (50 limit, case-insensitive keys/case-sensitive values), native facts atomic with state, no-op suppression.
- [ ] Test DELETE202/poll lifecycle, lease loss, restart, repeated requests, deletion versus import/assignment race, no cross-group deletes and no false success on failed jobs.
- [ ] Run new integration tests for RED.
- [ ] Implement selected routes and durable deletion with final-state/evidence/job commit fencing, ARM errors/request IDs and local-only polling links. No catch-all provider success.
- [ ] Run affected inventory, authorization, job and evidence regressions; commit.

### Task 4: Public role definitions and assignments

**Files:** Create providers/arm/{rbac,rbac_routes}.py; tests/integration/test_arm_rbac.py; update docs/arm-contracts.md.
**Interfaces:** role_view(definition,scope)->dict; assignment_view(row)->dict; mutate_assignment(session,actor,scope,assignment_id,body,operation,now)->result; uses Task2 authority and Task3 append_arm_fact.

- [ ] Test actual roleDefinitionId/principalId/principalType shape, assignment PUT idempotency and conflicting immutable identity, duplicate concurrent grants, current transitive memberships, scope-restricted GET/list/DELETE and atScope()/principalId filters.
- [ ] Test conditions/custom role writes/foreign principals rejected, Contributor self-elevation denied, Owner grant/revoke followed by denied continuation and operation polling.
- [ ] Run tests/integration/test_arm_rbac.py for RED.
- [ ] Implement public routes and full canonical integration with legacy admin Reader endpoints; emit native facts only for real changes.
- [ ] Run all ARM tests plus affected directory membership/identity tests; commit.

### Task 5: Immutable Activity Logs and SQL paging

**Files:** Create providers/arm/{activity,activity_query,activity_routes}.py; update evidence/{schemas,models,projectors,projection,repository,datasets,cleanup}.py and admin/evidence_routes.py; migration031+; tests/unit/test_arm_activity_contract.py; tests/integration/test_arm_activity.py,test_arm_activity_paging.py.
**Interfaces:** project_arm(SourceEnvelope)->ProjectionBatch with activityLog EventData; compile_activity_filter(text)->SQL predicate; read_activity(session,actor,subscription,query,cursor,now)->{value,nextLink?}; family remains excluded from Graph routes.

- [x] Test exact EventData projections/imports with UUID event identity, same transaction native facts, provenance, captured caller/action/scope/correlation and no credential/IP fabrication.
- [x] Test SQL time/resourceGroup/resourceUri/provider/correlation filters/select and1003 records with equal timestamps, exact instants, permission filtering before paging, generation reset, retention pinning, expiry and role removal between pages.
- [x] Run new tests for RED; include native group/tag/RBAC/deletion effects and failed terminal operation evidence.
- [x] Extend shared evidence publication/receipts/export/retention and fenced worker; add explicit Activity endpoint and opaque continuation binding. Apply observed MES date grammar only as a documented bounded compatibility rule.
- [x] Run ARM/evidence/retention/worker regressions and query-plan checks (indexed pagination, no full Python materialization); commit.

### Task 6: Actual SDKs and original MES qualification

**Files:** Create compose.m5.yaml,compose.clients.m5.yaml,deploy/haproxy-m5.cfg; tools/{prepare_arm,prepare_arm_evidence,verify_arm,qualify_arm_collectors}.py; integrations/{python/arm.py,powershell/Test-ArmManagement.ps1,powershell/Test-ArmCollectors.ps1,powershell/Arm-LocalTransport.ps1}; update manifest/uv.lock with pinned official client versions; tests/unit/test_arm_report.py; tests/acceptance/test_arm_distribution.py.
**Interfaces:** verify_arm writes source/lock/schema/archive/wheel/image/output-bound report with literal success per runtime/client/collector case. Original Get-ActivityLogs source revision47565f7842784cd3667ff630e3dccb49fa3408ed unchanged.

- [ ] Write RED report/oracle tests for missing fields, lost timestamps, missing/duplicate IDs, malformed arrays, denied disclosures and artifact hash/path escape.
- [ ] Provision fresh own CA/config/database and deterministic1003 historical Activity events plus native changes; independently record expected IDs/times/scopes. Reuse only manifest-verified named public archives.
- [ ] Pin actual Az.Resources and Python management SDK contracts; run app/delegated discovery, groups/tags/RBAC through original SDKs in local-only transport. Observe exact API-version/paths before adding source-backed compatibility routes.
- [ ] Run original MES through discovered and explicit subscription paths, both auth modes, positive and denied cases; actual Get-AzAccessToken/Get-AzSubscription unchanged. Parse and verify every output artifact. No repaired exports or replaced cmdlets.
- [ ] Run clean-wheel native/container, two-worker/API, restart/lease/fault and DNS/IPv4/IPv6 egress-denial checks; preserve failed reports and nonzero exit rather than lowering gates.
- [ ] Run focused report/acceptance tests, archive measured result and commit.

### Task 7: Regression, one final review and M6 handoff

**Files:** README.md; docs/{capabilities,compatibility,arm-execution,arm-decisions,overnight-execution}.md; plan; review report.
**Interfaces:** Consumes Task1–6 evidence; produces bounded capability matrix, honest acceptance status and separate feature PR. M6 inherits current code plus unresolved predecessor gates.

- [ ] Run full unit/integration suite in own PostgreSQL runtime; run current source-bound qualification and Ruff/diff/package/provenance checks. Expect all product tests PASS; retain any external collector defects as unresolved, never new waivers.
- [ ] Dispatch exactly one fresh final reviewer with spec/plan/ledger/range and five Review Focus items; regrade, fix Important/Critical in one RED→GREEN pass and rerun affected/full gates. Ledger every ruling/deferred judgment.
- [ ] Update capability discovery and source-bound docs, export ledger, commit/push feature branch, create/attach PR (draft if any mandatory gate unresolved). User merges.
- [ ] Continue M6 package/parity/cleanup/scale/release preparation under existing authorization; do not publish packages or claim combined acceptance while a required predecessor gate fails.

## Plan self-review and adopted rulings

All scope sections map to Tasks1–6 and all Review Focus items have tests.
Task2 supplies canonical state/RBAC to Tasks3–5; Task3 supplies native facts/jobs;
Task4 uses the same assignment authority; Task5 adds an evidence family without
exposing it through Graph; Task6 consumes explicit wire contracts. Deletion
events must not be projected as completed at request acceptance.
Predecessor module paths were verified during planning. Exact client package
versions are pinned during acquisition, before any client integration executes. No dependency on a real tenant.
The user delegated reasonable overnight decisions; this plan is adopted for
Native execution without asking for repeated approvals.
