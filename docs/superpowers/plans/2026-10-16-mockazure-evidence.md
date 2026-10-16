# MockAzure M3B Evidence and Live API Simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make real local authentication, managed-identity, licence and risk API calls produce durable evidence that genuine SDKs and pinned Hawk/MES collectors can retrieve correctly.

**Architecture:** Extend M3A facts and M2 authentication outcomes into a typed PostgreSQL projection pipeline with fenced jobs, immutable observations and versioned provider views. Reuse identity authorization and the query AST; add explicit evidence contracts, licence state and one complete managed-identity hosting protocol. Separate evidence time from authentication and continuation time.

**Tech Stack:** Python >=3.12, existing FastAPI/SQLAlchemy2/psycopg3/Alembic/PostgreSQL18, cryptography/PyJWT, HTTPX, Typer, pytest. Keep MSAL1.39.0, msgraph-sdk1.64.0, Azure Identity1.26.0 and existing PowerShell pins.

**Spec:** [Approved M3B design](../specs/2026-10-16-mockazure-evidence-design.md).
**Normative appendix:** [M3B API and state contract](../../../integrations/contracts/graph-evidence.md), sections1–8; read with this plan.
**Status:** Written for review after design approval, 2026-10-16. No M3B product implementation.
**Execution:** Native, already selected; main agent implements, one independent whole-change review.
**Base:** merged main4a5fedb plus approved design406901f and this plan; create an isolated D: worktree at execution. No dependency on an unmerged M2/M3A PR.

## Global Constraints

- All work stays under D:/projects/mockazure. Do not access VTW.
- Preserve M1, M2 and M3A runtime files, databases, images and networks.
- No real Microsoft tenant traffic, upstream fallback or changes to host trust, DNS, hosts, firewall or profiles.
- Public documentation and dependency acquisition remain separate from isolated runtime qualification.
- Normal use requires no scenario. Historical imports are optional administrative inputs with provenance.
- Missing endpoint implementations are work to complete when needed for the approved flows, not reasons for canned results or import-only substitutes.
- M3C policy/grant/role administration, M4 Exchange/UAL and M5 ARM remain separate; existing behavior stays a regression gate.
- Evidence defaults: delay5s(0..3600), retention30days(1..3650), wall/fixed evidence clock, continuation1wall-clock hour.
- Evidence imports:10MiB/10000 records; provider JSON:1MiB; strict public schema, no credentials/private keys/sessions.
- Evidence cursor generation is independent of directory delta generation. No evidence delta in this increment.
- PostgreSQL is the system of record. No SQLite, Redis or broker dependency.
- Commit coherent increments, push, create/attach a reviewable PR against main. Never merge or force-push on the user's behalf.
- No implementation until written-plan review. Retain Native without asking the execution-method question again.

## Review Focus

1. Stale projector finishes after retry/reset/lease takeover and resurrects or duplicates old evidence: Tasks1,2,9.
2. P2-to-P1 downgrade or self-only OR filter exposes hidden tenant/risk data through a continuation or predicate: Task7.
3. One-seat concurrent assignment or deleted-user licence release drifts from Graph fields, delta or entitlement state: Task4.
4. Browser/device success double-counts, or rollback erases a denied sign-in while committing partial OAuth state: Task5.
5. Risk dismissal races a delayed sign-in projection and the old event reopens risk; original collector emits missing/duplicate/invalid files despite success output: Tasks8,11.

## File and responsibility map

Paths are under src/mockazure unless prefixed otherwise. New package directories include __init__.py.
Existing files are extended at named interfaces; do not move unrelated M2/M3A code.

| Unit | New files | Existing integration points |
| --- | --- | --- |
| Evidence storage/types | evidence/{models,schemas,repository,clock}.py; db/alembic/versions/011_evidence_jobs.py | directory/models.py, oauth/device_models.py, db/job_model.py, db/alembic/env.py |
| Projection runtime | evidence/{sources,projection,projectors}.py; jobs/dispatch.py | jobs/{schemas,repository,worker}.py |
| Source facts | evidence/{directory_facts,authentication}.py | directory/{history,mutations,types}.py; oauth/{authorization,device,routes,sessions,client_auth}.py |
| Licensing | licensing/{models,schemas,catalog,service}.py; providers/graph/licensing.py; admin/licensing_routes.py; commands/licensing.py; migration012_licensing.py | directory/{types,mutations,datasets}.py; providers/graph/{serialization,permissions}.py; authorization/catalog.py |
| Managed identity | managed_identity/{models,schemas,service,app}.py; admin/managed_identity_routes.py; commands/managed_identity.py; migration013_managed_identity.py | identity/{models,types,repository,tokens}.py; config.py; cli.py |
| Evidence reads | providers/graph/evidence/{routes,contracts,query,permissions,serialization,paging}.py | providers/graph/query/{compiler,contracts}.py; providers/graph/{batch,throttle}.py; admin/app.py |
| Risk state/actions | evidence/risk.py; providers/graph/evidence/risk_actions.py | evidence/projectors.py, evidence/schemas.py |
| Operations/datasets | evidence/{datasets,cleanup}.py; admin/evidence_routes.py; commands/evidence.py | admin/client.py, cli.py, jobs/repository.py |
| Qualification | tools/{prepare_evidence,verify_evidence}.py; compose.m3b.yaml; compose.clients.m3b.yaml; deploy/haproxy-m3b.cfg; integrations/python/{managed_identity,graph_evidence}.py; integrations/powershell/{Graph-LocalTransport,Test-GraphEvidence,Test-GraphCollectors}.ps1 | tools/prepare_clients.py; integrations/manifest.json; README.md; docs/{capabilities,compatibility}.md |
| Tests | files named in tasks; tests/integration/evidence_support.py | existing identity/directory/job regression tests |

Actual directory serialization is providers/graph/serialization.py; do not create
directory/serialization.py as an alternate serializer. Keep read/write authorization
and source capture separate modules.

## Shared types, transaction and compatibility decisions

Task1 owns:
- EvidenceFamily = Literal["signIn","directoryAudit","riskDetection","riskyUser"].
- SourceRef(tenant_id:UUID,kind:str,id:UUID), frozen.
- SourceInput(ref:SourceRef,occurred_at:datetime,available_at:datetime,facts:dict),
  caller input without storage-assigned counters; strict nonsecret validated payload.
- SourceEnvelope(ref:SourceRef,source_revision:int,generation:UUID,occurred_at:datetime,
  available_at:datetime,facts:dict), strict nonsecret validated payload.
- Projection(family:EvidenceFamily,id:UUID,occurred_at:datetime,available_at:datetime,
  payload:dict,subject_id:UUID|None,source_ref:SourceRef), immutable input.
- PublishResult(revision:int,record_ids:tuple[UUID,...],already_applied:bool).
- EvidenceSettings(delay_seconds:int,retention_days:int,clock_mode:Literal["wall","fixed"],
  fixed_time:datetime|None), defaults from global constraints.
- ProjectionBatch(source:SourceRef,items:tuple[Projection,...],receipt_status:
  Literal["projected","suppressed","legacy_incomplete"]), consumed by Task2.

Task2 owns JobClaim(id:UUID,tenant_id:UUID,kind:str,owner:UUID,attempt:int,epoch:int,
lease_until:datetime,payload:dict) and JobResult(state:str,result:dict|None,error_code:str|None).
Preserve tenant.export's wire JobView and ExportSnapshot; generalized internals do not
make an evidence result readable through the old export-result endpoint.

Use AsyncSession as Session below; every domain service accepts an existing caller-owned
transaction and never commits internally unless its signature explicitly accepts Database
for a separate failure-recording transaction. Datetimes are aware UTC. UUIDs are not strings
until wire serialization. All lookup/update/delete predicates include tenant_id.

Lock order and defaults are appendix8. Avoid separate durable enqueue-only transactions
inside a domain mutation. Adapters deduplicate pending legacy sources; source ingestion and
job enqueue share a commit. Worker claim is short; projection starts a new transaction,
locks EvidenceState then validates/locks its job fence before writing. Fence includes
unexpired lease, owner, attempt, epoch and current generation. Retry increments epoch.
Do not reset a generation or retry counter without invalidating old workers.

Risk transitions need read-your-write current state and versioned public overlays.
Their domain transaction updates state, views and action source; asynchronous projection
adds only the corresponding audit. Projectors must not overwrite newer overlays when
older source facts arrive. Evidence retention cannot delete necessary action cutoffs.

The shown test snippets are decisive assertions after each step's stated arrangement;
use existing API/database fixtures and the shared evidence_support helpers, not test-only
production branches. Unit and integration modules have distinct names to preserve pytest imports.

## Task 1: Upgrade-safe evidence storage, clocks and receipts

**Files:** Create evidence/{models,schemas,repository,clock}.py, migration011_evidence_jobs.py,
tests/integration/{evidence_support,test_evidence_storage}.py.
Modify db/job_model.py, db/alembic/env.py and outcome/outbox models only for declared source metadata.

**Interfaces:**
- Produces append_source(session:Session,value:SourceInput)->SourceRef,
  load_source(session:Session,ref:SourceRef)->SourceEnvelope|None in repository.py.
  append_source allocates source_revision and captures current generation under the tenant
  EvidenceState lock; callers cannot assign either counter.
- Produces publish_batch(session:Session,batch:ProjectionBatch)->PublishResult;
  caller must hold current generation/fence. Repository validates unique receipt and payload.
- Produces evidence_now(session:Session,tenant_id:UUID,wall_now:datetime)->datetime and
  update_settings(session:Session,tenant_id:UUID,settings:EvidenceSettings)->EvidenceSettings.
- Models: EvidenceState, EvidenceSource, EvidenceObservation, EvidenceVersion,
  ProjectionReceipt, EvidenceCursor, RiskActionCutoff. Composite tenant constraints;
  UUID version keys and indexed family/time/user/app/type; job claim_epoch and source dedupe key.

- [ ] Write test_upgrade_m3a_preserves_state_and_pending_sources: migrate populated010
  with current user/group/delta handle, pending directory facts and M2 outcome/export job.
  Existing IDs, secrets/grants and nextLink remain usable; no retroactive fabricated rows.
  assert directory_cursor_after == directory_cursor_before; assert legacy_export.payload == before.
- [ ] Write test_publication_commit_order_and_rollback: hold tenant A publication lock,
  ensure competing publisher cannot expose higher revision first; rollback leaves no
  observation/version/receipt. assert not await visible_record_ids(tenant_a).
- [ ] Write test_tenant_constraints_and_replay_receipt: cross-tenant links fail;
  same source replay keeps IDs/count; deleting expired views then replay does not resurrect.
  assert second.record_ids == first.record_ids; assert second.already_applied.
- [ ] Write test_clock_is_separate: fixed evidence clock advances while token and cursor
  expiry use wall time; rewind rejected. Assert no OAuth clock value changes.
- [ ] Run .venv/Scripts/python.exe -m pytest tests/integration/test_evidence_storage.py -q.
  Expected RED: missing011/schema/interfaces or invariant failures.
- [ ] Implement the named schema/interfaces, explicit public validators and source/receipt
  uniqueness. Migration preserves existing tables/data and defaults, never reprovisions tenants.
- [ ] Run that file plus tests/integration/test_graph_directory_storage.py,
  test_identity_upgrade.py and test_jobs.py; expected all PASS.
- [ ] Commit: feat(evidence): add tenant-scoped observations and publication history.

## Task 2: Typed jobs and fenced, duplicate-safe projection

**Files:** Create jobs/dispatch.py, evidence/{sources,projection,projectors}.py,
tests/integration/test_evidence_worker.py. Modify jobs/{schemas,repository,worker}.py.

**Interfaces:**
- Consumes Task1 SourceRef,SourceEnvelope,ProjectionBatch,publish_batch/load_source.
- Produces enqueue_projection(session:Session,ref:SourceRef)->UUID,
  scan_sources(session:Session,*,limit:int=100)->int in sources.py.
- Produces project_source(source:SourceEnvelope)->ProjectionBatch in projectors.py
  (dispatches exact source kind; task3/5/8 register real mappings).
- Produces process_projection(database:Database,claim:JobClaim,wall_now:datetime)->JobResult.
- claim_job returns JobClaim; dispatch_job(database:Database,claim:JobClaim,wall_now:datetime)
  ->JobResult. Existing run_one(database,owner,now)->bool remains the worker boundary.

- [ ] Write test_mixed_jobs_keep_export_contract: an export and evidence job both finish;
  old export API still returns ExportSnapshot, evidence job there is404.
- [ ] Write test_worker_takeover_and_atomic_receipt: kill a worker after claim and before
  commit; another worker takes expired lease. assert visible_count == 1 and receipts == 1.
- [ ] Write test_retry_epoch_and_reset_fence: old claim cannot finish after retry or reset,
  even if attempt numbers repeat. assert stale_result.state == "stale".
- [ ] Write test_failure_classes_and_scanner_repair: transactionally persisted but unenqueued
  source gets one job; transient retry1s/2s then fails at3; malformed schema fails once
  with sanitized error; tenantB proceeds despite poisoned tenantA source.
- [ ] Run pytest tests/integration/test_evidence_worker.py -q; expected RED from export-only dispatch.
- [ ] Implement kind registry, row-lock fence/lease checks and atomic publish/receipt/
  acknowledgement/completion. Drain and background worker will call this same dispatcher.
  No external I/O in projection. Add bounded scanner to worker loop without starving exports.
- [ ] Run new tests plus tests/integration/test_jobs.py test_worker_processes.py test_exports.py;
  expected PASS, including process-interruption tests.
- [ ] Commit: feat(jobs): dispatch fenced evidence projections alongside exports.

## Task 3: Directory operations produce historically correct audits

**Files:** Create evidence/directory_facts.py, tests/integration/test_evidence_directory.py.
Modify directory/{types,history,mutations}.py and evidence/projectors.py.

**Interfaces:**
- Consumes Task1/2 append_source/enqueue_projection and source adapters.
- Produces capture_directory_facts(session:Session,actor:PrincipalContext|AdminActor,
  command:DirectoryCommand,before:DirectoryObject|None,after:DirectoryObject|None,
  correlation_id:UUID,relationships:list[dict])->dict in directory_facts.py.
- Produces project_directory(source:SourceEnvelope)->ProjectionBatch in projectors.py.

- [ ] Write test_directory_audit_mapping: create/update/enable/disable/delete and membership
  operations produce appendix5 activity names, exact actor/targets and JSON-string differences.
  assert len(audits_for(source_id)) == 1; assert audit["result"] == "success".
- [ ] Write test_rename_delete_history_cleanup_keeps_event_labels: rename actor/target after
  mutation, prune directory history, then project; old values/labels still match original facts.
- [ ] Write test_import_admin_and_noop_origins: directory import acknowledged with zero audit;
  admin create has empty initiatedBy plus internal provenance; unchanged PATCH has no audit.
  Failed/rolled-back Graph mutation has neither success audit nor changed public state.
- [ ] Write test_legacy_pending_fact_adapter: old facts preserve known IDs/differences but
  do not attach current names or guess app/user actor kind; two scans remain idempotent.
- [ ] Run pytest tests/integration/test_evidence_directory.py -q; expected RED.
- [ ] Implement v2 facts and explicit activity map. Preserve directory outbox provenance used
  by existing datasets. Capture request correlation at source; scrub fields before persistence.
  No extra membership audit for implicit deletion cascade or initial group members.
- [ ] Run new file, test_graph_mutations.py, test_graph_delta.py and test_graph_datasets.py;
  expected PASS and no credential material in recursively serialized facts/audits.
- [ ] Commit: feat(evidence): project committed directory changes into audit history.

## Task 4: Stored licences and real Graph assignment effects

**Files:** Create licensing/{models,schemas,catalog,service}.py, migration012_licensing.py,
providers/graph/licensing.py, admin/licensing_routes.py, commands/licensing.py,
tests/integration/test_graph_licensing.py. Modify directory/{types,mutations,datasets}.py,
providers/graph/{serialization,permissions}.py, authorization/catalog.py, admin/app.py,
admin/client.py, cli.py, evidence/projectors.py.

**Interfaces:**
- Produces upsert_sku(session:Session,tenant_id:UUID,value:SkuInput)->SkuView.
  SkuInput/SkuView use appendix7 inventory schema and section3 fields.
- Produces assign_licences(session:Session,actor:PrincipalContext,user_id:UUID,
  command:LicenceAssignment,now:datetime,correlation_id:UUID)->DirectoryObject.
  LicenceAssignment has addLicenses/removeLicenses matching appendix4.
- Produces entitlements(session:Session,tenant_id:UUID,user_id:UUID|None)->Entitlements
  with tenant_tier Free/P1/P2,user_plan_ids:frozenset[UUID],fingerprint:str.
- Consumes Task3 audit facts; uses existing directory revision/version/change services.

- [ ] Write test_license_api_read_write_matrix: all appendix1 permission/role alternatives,
  denied app licenseDetails, foreign users, usageLocation write, subscriber read projection.
- [ ] Write test_assignment_updates_inventory_delta_and_entitlements: add/update plans/remove
  produces matching assignedLicenses/assignedPlans, consumedUnits, selected-user delta and
  one Change user license audit. assert repeated_noop.delta == [].
- [ ] Write test_last_seat_concurrency_and_delete_release: two users race one seat, exactly
  one succeeds; deleting winner releases it; SKU shrink below consumption fails.
- [ ] Write test_assignment_atomic_validation: invalid location/SKU/disabled plan/duplicate/
  conflicting add-remove/privileged target leaves all counters/versions/facts unchanged.
  P2 disabled on one SKU but enabled on another retains effective P2.
- [ ] Run pytest tests/integration/test_graph_licensing.py -q; expected RED unsupported routes.
- [ ] Implement direct assignments and usageLocation with fixed country catalog; lock
  DirectoryState then affected SKUs by UUID. Tie deletes to seat release without duplicate
  outbox revisions. Provider response200 includes licensed user fields. Never issue app
  permission merely by assigning a licence. Preserve datasetv1 through explicit old field
  projection; document that it omits new licence/usage fields rather than breaking import.
- [ ] Run new tests plus test_graph_mutations.py,test_graph_delta.py,test_graph_datasets.py
  and test_identity_authorization.py; expected PASS.
- [ ] Commit: feat(graph): enforce persistent direct licence assignments.

## Task 5: Live authentication captures durable success and denial

**Files:** Create evidence/authentication.py, tests/integration/test_evidence_authentication.py.
Modify oauth/{authorization,device,routes,sessions,client_auth}.py, oauth/device_models.py,
evidence/sources.py and evidence/projectors.py.

**Interfaces:**
- Produces record_authentication(session:Session,fact:AuthenticationFact)->SourceRef.
  AuthenticationFact: attempt_id,tenant_id,client_id?,subject_id?,flow,event_type,occurred_at,
  resource?,correlation_id,outcome,error_code,actor_snapshot,client_snapshot,ip_address?,mfa.
- Produces record_authentication_failure(database:Database,fact:AuthenticationFact)->None:
  opens a new transaction only after original rollback.
- Produces project_authentication(source:SourceEnvelope)->ProjectionBatch.
- Consumes Task1/2 source and job interfaces; appendix5 defines exact terminal mapping.

- [ ] Write test_live_auth_types_and_single_device_success: actual code/device/refresh/client
  credentials flows yield interactive/noninteractive/servicePrincipal as mapped; device
  approval+redemption has one interactive success, replay adds none.
- [ ] Write test_denial_survives_rollback_without_oauth_state: wrong password, disabled user,
  consent/MFA refusal and known invalid client secret keep one classified failure despite
  rollback; assert new_sessions == new_grants == new_tokens == 0.
- [ ] Write test_unknown_and_internal_failures_not_fabricated: malformed/unknown tenant,
  CSRF/code/refresh-handle, device polling and signing failure do not create success logs.
  Inject failure persistence outage: assert response.status_code == 503.
- [ ] Write test_legacy_device_outcomes_not_double_counted and test_common_authority_tenant:
  map only known tenant/subject, including null subject for tenant-local unknown username.
  Capture bounded trusted IP/correlation; secrets and raw usernames beyond limits rejected.
- [ ] Run pytest tests/integration/test_evidence_authentication.py -q; expected RED.
- [ ] Implement explicit result/failure classification instead of catching all exceptions
  into audit success. Preserve existing OAuth wire errors, consent grants and session logic.
  Stable attempt IDs are server-controlled; client correlation does not deduplicate attempts.
- [ ] Run new tests and all tests/integration/test_identity_*.py; expected PASS.
- [ ] Commit: feat(identity): persist authentication evidence across success and denial.

## Task 6: Real managed-identity hosting and SDK token acquisition

**Files:** Create managed_identity/{models,schemas,service,app}.py,
migration013_managed_identity.py, admin/managed_identity_routes.py,
commands/managed_identity.py, integrations/python/managed_identity.py,
tests/integration/test_managed_identity.py. Modify identity/{models,types,repository,tokens}.py,
config.py,cli.py,admin/app.py,admin/client.py and evidence/authentication.py.

**Interfaces:**
- Produces create_managed_identity(session:Session,tenant_id:UUID,value:ManagedIdentityInput)
  ->ManagedIdentityView; create_host/update_host/rotate_host_header with appendix7 schemas.
- Produces acquire_managed_token(session:Session,request:ManagedTokenRequest,settings:Settings,
  now:datetime)->ManagedTokenResponse. Request includes header,selector,resource,api_version;
  response is appendix6, no ambient identity fallback.
- Produces create_managed_identity_app(settings:Settings,database:Database|None=None)->FastAPI.
- CLI: mockazure managed-identity serve --config FILE; separate configured host/port.
  Shared identity rows use client_mode managed; ordinary secret/certificate creation rejects it.

- [ ] Write test_host_protocol_matrix: system default and all four selectors, duplicate keys,
  conflicting selectors, wrong resource/version/header, disabled/unattached/foreign identity.
  assert app_only_secret_route_for_managed.status_code in (400,401).
- [ ] Write test_managed_identity_sdk_end_to_end using installed genuine
  azure.identity.ManagedIdentityCredential (no token mock): acquire, validate claims and use
  local Graph organization/users allowed and denied calls; exact managedIdentity evidence.
  A bad host header must produce an SDK exception with a nonempty sanitized description,
  using the host protocol statusCode/message fields rather than an empty SDK error.
- [ ] Write test_header_rotation_and_detach_semantics: old header fails new acquisitions;
  existing tokens follow existing lifetime rules; system detach disables its SP, user detach
  affects only that host. Never disable another host's user-assigned identity.
- [ ] Write test_no_fallback_and_secret_output: hostile resource URI never gets fetched;
  managed header appears only in provisioned private file, never logs/public exports.
- [ ] Run pytest tests/integration/test_managed_identity.py -q; expected RED.
- [ ] Implement appendix6 and shared issuer/grants/subject checks, no second authentication
  store. Extend settings with explicit managed_identity_listen_host/port defaults127.0.0.1/49446;
  allow internal container binding only in its explicit config. JWT xms_mirid from persisted ID.
  Block client credential creation by managed mode in all relevant admin paths.
- [ ] Run new tests and identity token/app-only/CLI regressions. Record actual wire contract
  from Azure Identity1.26.0/MSAL1.39.0; do not change client to accommodate a fake response.
- [ ] Commit: feat(identity): emulate local managed-identity token hosting.

## Task 7: Evidence reads, queries, masked projections and stable pages

**Files:** Create providers/graph/evidence/{routes,contracts,query,permissions,serialization,paging}.py,
tests/unit/test_evidence_query_contract.py, tests/integration/test_graph_evidence_reads.py.
Modify providers/graph/query/{compiler,contracts}.py, providers/graph/{batch,throttle}.py,
authorization/catalog.py,admin/app.py.

**Interfaces:**
- Produces EvidenceAccess(subject_id:UUID|None,entitlement_fingerprint:str,redaction:str)
  and authorize_evidence(session:Session,context:PrincipalContext,operation:EvidenceOperation)
  ->EvidenceAccess. EvidenceOperation fields:version,resource,method,target_id?.
- Produces evidence_contract(version:str,resource:str)->QueryContract.
- Extend compile_filter(node,contract,variables=None,*,resolver:ColumnResolver|None=None)
  ->ColumnElement[bool]; ColumnResolver(FieldContract)->ColumnElement. None preserves A.
  Structured collections have a separate declared element-field resolver, not raw JSON paths.
- Produces read_evidence(session:Session,request:EvidenceRead)->EvidencePage:
  request context,version,family,query,token?,origin,wall_now; page value,next_link?.
- Produces snapshot_evidence(session:Session,tenant_id:UUID,wall_now:datetime)->EvidenceSnapshot
  (generation,revision,evidence_time,retention_cutoff,expires_at) for Task9 exports.

- [ ] Write test_routes_query_and_schema_matrix covering appendix1–3, beta explicit types,
  v1interactive default, equal-time order, required fields and null unknowns.
- [ ] Write test_sql_filters_match_original_mes_predicates: startsWith nested actor UPN,
  targetResources/any with child paths, event-type any OR, quoting/Unicode/%/_ escaping;
  NUL/depth17/>256nodes/huge numeric/bad path fail Graph400. Parameterized SQL only.
- [ ] Write test_self_scope_and_redaction_oracle: self OR foreign query returns self only;
  P1 filter on riskLevelDuringSignIn eq high cannot reveal stored high risk; masked fields
  have matching serialization/query behavior. Role removal and licence downgrade deny or
  invalidate old continuation before reading snapshots.
- [ ] Write test_evidence_snapshot_late_publish_and_risk_version: paginate10000 rows while
  adding backdated/delayed evidence and changing risk; exact original ID/property set remains.
  Concurrent committed publication ordering, replay across replica/restart and expiry verified.
- [ ] Write test_evidence_batch_and_budget: mixed directory/evidence GETs each authorize;
  separate600/60s per tenant/client/family fixed budgets, real Retry-After; fault next_read_429
  usable for each evidence family. No evidence write batches added.
- [ ] Run new unit/integration files; expected RED.
- [ ] Implement resource-specific SQL resolver using existing AST/startswith; version/cursor
  store independent of directory generations. Share hashing/serialization helpers without
  changing existing cursor schema. Add evidence resource allowlist to batch. Register all
  item/collection routes explicitly; wrong versions/methods remain explicit unsupported errors.
- [ ] Run new tests plus test_graph_query.py,test_graph_paging.py,test_graph_batch_limits.py,
  test_graph_review_regressions.py and test_graph_delta.py; expected PASS.
- [ ] Commit: feat(graph): expose permission-checked evidence queries and snapshots.

## Task 8: Risk observations and real confirmation/dismissal actions

**Files:** Create evidence/risk.py,providers/graph/evidence/risk_actions.py,
tests/integration/test_graph_risk_actions.py. Modify evidence/{schemas,projectors}.py,
providers/graph/evidence/routes.py and authorization/catalog.py.

**Interfaces:**
- Produces record_risk_observation(session:Session,tenant_id:UUID,value:RiskObservation)
  ->RiskObservationResult(detection_id:UUID,user_id:UUID,revision:int).
  Input is appendix7; request_id provides same-content idempotency.
- Produces apply_risk_action(session:Session,actor:PrincipalContext,action:
  Literal["confirmCompromised","dismiss"],user_ids:tuple[UUID,...],now:datetime,
  correlation_id:UUID)->None.
- Consumes Tasks1/3 publication and audit facts; Task7 permissions/snapshot readers.
  Pure overlay function apply_risk_cutoff(payload:dict,cutoff:RiskActionCutoff|None)->dict
  is shared by late projection and current updates.

- [ ] Write test_confirm_and_dismiss_visible_effects: actual POST204 changes user/detection/
  sign-in aggregate state according to appendix4 while preserving during-sign-in risk and
  original facts; snapshot pages remain at old versions; audit has public change facts.
- [ ] Write test_delayed_projection_cannot_undo_dismissal: hold old sign-in projection,
  dismiss, then complete it; old risk stays closed; a post-cutoff observation raises risk again.
- [ ] Write test_risk_role_license_and_atomic_batch: P1, missing write scope, unsupported role,
  foreign/unknown IDs, duplicate list/>100/body extras rejected with no partial state.
- [ ] Write test_observation_idempotency_and_cross_links: same request+content gives same IDs;
  changed content409, mismatched user/sign-in or foreign subject fails. Risk does not alter
  conditional access/login before C.
- [ ] Run pytest tests/integration/test_graph_risk_actions.py -q; expected RED.
- [ ] Implement shared risk state service, indexed cutoffs/version overlay and append-only
  action sources. Do not synthesize a proprietary risk score or fabricate provider state
  merely to satisfy a collector's risky-record branch.
- [ ] Run new tests plus test_graph_evidence_reads.py,test_evidence_worker.py and relevant
  identity denial tests; expected PASS.
- [ ] Commit: feat(graph): implement persistent risk confirmation and dismissal.

## Task 9: Evidence administration, safe datasets and cleanup

**Files:** Create evidence/{datasets,cleanup}.py,admin/evidence_routes.py,commands/evidence.py,
tests/integration/test_evidence_operations.py. Modify admin/{app,client}.py,cli.py,
jobs/repository.py and docs/evidence.md.

**Interfaces:**
- Produces import_evidence(session:Session,tenant_id:UUID,dataset:EvidenceDataset)->ImportResult
  (inserted,unchanged,record_ids), schema from appendix7.
- Produces export_evidence(session_factory,tenant_id:UUID,query:EvidenceExportQuery)
  ->AsyncIterator[bytes]; bounded500-row chunks and persisted snapshot pin.
- Produces cleanup_evidence(session:Session,tenant_id:UUID,wall_now:datetime,limit:int=500)->dict,
  reset_evidence(session:Session,tenant_id:UUID,expected_generation:UUID)->UUID.
- Produces retry_projection(session:Session,tenant_id:UUID,job_id:UUID,wall_now:datetime)->JobView
  and drain_evidence(database:Database,tenant_id:UUID,max_jobs:int=100)->DrainResult.
- All administrative routes/CLI exactly appendix7; reuse Task2 dispatch.

- [ ] Write test_dataset_roundtrip_and_conflict_atomicity: exact IDs/payload/provenance across
  fresh database, identical replay idempotent, late invalid row rolls back all records,
  >10MiB/>10000 rejected before import, secrets/unknown properties rejected.
- [ ] Write test_export_stream_snapshot_under_change: mutate/cleanup during streaming;
  stable exported IDs and bounded fetch size; disconnect releases or expires pin.
- [ ] Write test_cleanup_retention_and_no_resurrection: fresh expired reads absent; active
  cursor still valid until wall expiry; physical cleanup then replay does not restore records.
- [ ] Write test_reset_generation_and_retry_tenant_scope: evidence reset preserves directory,
  delta,auth,licences; stale worker cannot publish old sources; future operations project;
  another tenant unchanged. Retry wrong kind/not-failed/cross-tenant fails.
- [ ] Write test_cli_live_api_parity for settings/clock/import/export/drain/jobs/retry/cleanup/
  reset and inventory/managed private provisioning; no direct DB bypass or secret stdout.
- [ ] Run pytest tests/integration/test_evidence_operations.py -q; expected RED.
- [ ] Implement admin/CLI and bounded cleanup/import/export. Retain projection receipts and
  action cutoffs while their observations/sources remain usable; make reset and scanner share
  generation locking. Incremental generation reset cleanup avoids unbounded request work.
- [ ] Run new tests plus test_exports.py,test_jobs.py,test_graph_datasets.py and
  test_identity_cli.py; expected PASS.
- [ ] Commit: feat(evidence): add safe administration and dataset lifecycle.

## Task 10: Isolated deployment and genuine SDK qualification

**Files:** Create tools/{prepare_evidence,verify_evidence}.py,compose.m3b.yaml,
compose.clients.m3b.yaml,deploy/haproxy-m3b.cfg,integrations/python/graph_evidence.py,
integrations/powershell/Test-GraphEvidence.ps1,tests/acceptance/test_evidence_distribution.py.
Modify integrations/manifest.json,tools/prepare_clients.py and preparation unit tests.

**Interfaces:**
- prepare_evidence.py --config-output PATH provisions only .runtime/m3b, DBmockazure_m3b,
  image mockazure:evidence, Compose project mockazure-m3b.
- verify_evidence.py --report PATH returns0 only with all required checks/profiles true;
  report schema_version1 includes source fingerprint,commit,manifest/lock/wheel hashes,
  image ID, qualified_at, durations, checks, clients, collector output hashes/counts.
- Python evidence client writes private local report; no printing tokens/headers.

- [ ] Add acceptance expectations and preparation integrity tests before verifier exists.
  Assert missing verifier/report fails, not a skip presented as a pass.
- [ ] Acquire/pin Microsoft.Graph.Reports,Microsoft.Graph.Identity.SignIns,
  Microsoft.Graph.Users.Actions at2.35.1, preserving Authentication/Users/Groups/
  Identity.DirectoryManagement2.35.1 and all M2 pins. Verify archive SHA and entire
  extracted trees. Retain Azure Identity1.26.0 from lock/manifest, make client dependency
  explicit without changing version. Downloads only in preparation.
- [ ] Run .venv/Scripts/python.exe -m pytest tests/unit/test_prepare_clients.py -q;
  new integrity/tamper tests should fail before preparation changes then pass after.
  Record actual acquisition hashes; never invent hashes in this plan.
- [ ] Provision separate loopback ports49443 HTTPS,55436 PostgreSQL,49444/49445 replicas,
  49446 managed identity, subject to ownership/free-port checks. If occupied allocate the
  next free owned range and record it; never stop another instance. New internal networks
  select verified-free subnets, preserving existing networks. Persist all volumes underD:.
- [ ] Run new acceptance on the ready isolated instance; expected RED missing verifier/client
  interfaces. Keep real API boot, not a fabricated report fixture.
- [ ] Implement verifier and genuine SDK flows in both app/delegated modes where supported:
  sign-in/audit/risk reads, licensing add/remove, risk confirmation/dismissal; delegated-only
  licenseDetails and explicit app denial. ManagedIdentityCredential native+container
  acquires tokens, calls actual Graph and verifies resulting managedIdentity records.
- [ ] Verify clean wheel/native CLI and container API, two API replicas/two workers,
  network denial DNS/directIPv4/directIPv6, actual API/worker/PG restarts. Assert runtime
  package bytes match wheel/source fingerprint; no stale report success.
- [ ] Run verifier SDK phase and acceptance applicable gates; expect all implemented SDK
  gates PASS, collector gates remain explicitly pending until Task11 (overall report not passed).
- [ ] Commit: test(evidence): qualify isolated SDK and managed-identity workflows.

## Task 11: Original Hawk and MES collection output gates

**Files:** Create integrations/powershell/{Graph-LocalTransport,Test-GraphCollectors}.ps1,
tests/unit/test_evidence_collector_reports.py. Extend tools/verify_evidence.py,
tests/acceptance/test_evidence_distribution.py and docs/compatibility.md.

**Interfaces:**
- Graph-LocalTransport exposes a module-scoped request wrapper that rewrites only the exact
  HTTPS graph.microsoft.com origin to configured local Graph origin, preserves method/path/
  query/body/headers and invokes module-qualified Microsoft.Graph.Authentication\\Invoke-MgGraphRequest.
  Local origin already configured is passed unchanged; other origins/redirects rejected.
- Collector report: revision,mode,function,parameters,source_hashes,files[{path,sha256,
  record_ids,record_count}],expected_ids,retry_observations,denial_observations,passed.
- Unit report parser rejects missing/duplicate/unexpected IDs, invalid JSON/CSV, wrong
  sentinel fields and source-hash drift.

- [ ] Write failing parser tests with truncated, overwritten, concatenated JSON and duplicate
  file records; assert each produces passed=false rather than relying on process exit.
- [ ] Run unit parser tests; expected RED before verification parser.
- [ ] Load exact pinned collector functions/helpers. Hawk: Get-HawkUserEntraIDSignInLog,
  Test-GraphConnection,Test-HawkGlobalObject,Initialize-HawkGlobalObject,Test-UserObject,
  Out-LogFile,Out-MultipleFileType and their original helper closure. MES: original module's
  Get-GraphEntraSignInLogs,Get-GraphEntraAuditLogs and date/auth/output/log/merge helpers.
  Preserve source hashes; no substituted collectors or serializers. Configure original
  telemetry opt-out; if unavailable, a named telemetry-only no-op records calls and sends
  nothing, matching the approved isolation boundary. No service call may be stubbed.
- [ ] Implement local transport wrapper scoped to MES only; do not shadow generated
  Get-MgAuditLogSignIn or global provider cmdlets. Existing local Graph context passes genuine
  connection probes. Reject any reconnect/default-public-authority attempt; record failure.
- [ ] Run original Hawk with a recent explicit window and one user with >1000 interactive
  records, both risky/nonrisk; compare exact IDs and sentinel fields in CSV/JSON and
  investigation subset. Other users/type/time distractors must be absent.
- [ ] Run original MES all four event types with >1000 records per type, directory audits
  >1000 records, equal timestamps, actor-prefix/target filters and empty case. Validate original
  JSON and SOF-ELK outputs independently, including MergeOutput. Use the literal query
  semantics for target OR, with an outside-window sentinel to expose that client behavior.
- [ ] Inject persisted429 on a later page and verify actual SDK/collector retry plus full
  output IDs. Test permission/licence denial without silent empty-success qualification.
  No harness retry substitutes for collector retry. If upstream JSON concatenation or other
  output defect appears, preserve files/report and diagnose; do not change server query
  semantics, timestamps or page pacing to hide it. Report the concrete compatibility issue.
- [ ] Run unit parser tests and full collector qualification in both configured auth modes;
  require all declared output gates, original source integrity and isolation checks PASS.
  A failing collector gate keeps the milestone incomplete until explicitly resolved.
- [ ] Commit: test(collectors): validate original Graph evidence export contents.

## Task 12: Complete regression, review and publish

**Files:** Update README.md,docs/{capabilities,compatibility,evidence-execution}.md,
this plan and integrations/contracts/graph-evidence.md status only after verification.

- [ ] Run .venv/Scripts/python.exe -m pytest tests/unit tests/integration -q.
  Expected all PASS; do not run multiple pytest processes sharing work/pytest.
- [ ] Build a clean wheel with the existing uv offline workflow; rebuild only
  mockazure:evidence with current source fingerprint; restart only mockazure-m3b.
- [ ] Run .venv/Scripts/python.exe tools/verify_evidence.py --report work/evidence-report.json.
  Require nativePython,containerPython,containerPowerShell SDK profiles; native/container
  managed identity; Hawk and MES modes/output gates; exact 5000-user/10000-evidence IDs;
  two replicas/workers; API/worker/PostgreSQL interruption; stable paging; ingestion delay/
  retention; 429; denied/cross-tenant/licence cases; clean wheel and blocked external network.
  Report source/manifest/lock/wheel hashes and image IDs with actual durations/counts.
- [ ] Run .venv/Scripts/python.exe -m pytest tests/acceptance/test_evidence_distribution.py -q,
  .venv/Scripts/ruff.exe check ., .venv/Scripts/python.exe tools/check_package.py,
  and git diff --check; require exit0 and inspect output. Existing M1/M2/M3A regressions must
  pass from the new instance/code; do not mutate preserved instances to prove them.
- [ ] Perform one independent whole-change review under Native, focused on the five failure
  classes and full approved scope. Reproduce accepted defects RED, fix, rerun affected
  checks/full suite and requalify if runtime/client source changes. No success claim based
  only on process exit or a stale report.
- [ ] Record support boundaries, exact collector behavior, evidence IDs, complete decisions
  and costs, review outcomes and any blockers. No blanket "Graph compatible" or throughput
  claim beyond measured functional profile.
- [ ] Commit final verification/docs, push and create a PR targeting main; attach it to this
  chat. User handles merge. Keep worktree/runtime for feedback and preserve decision records
  before removing only this plan's temporary execution workspace.

## Self-review and handoff

Coverage:
- Durable evidence/time/history/upgrade:1–3,5,7,9.
- Real licence, managed identity and risk API state transitions:4,6,8.
- Permissions/roles/licences/query/redaction/continuations:4,7,8.
- Safe admin data operations, worker retry/reset/cleanup:1,2,9.
- Genuine SDK/native/container and pinned collector contents:10,11.
- Full regression/review/published evidence:12.

Inputs that need explicit local choices are frozen in the appendix: entitlement policy,
risk propagation/audit mapping, missing historical actor labels, error profiles and retention.
They are disclosed compatibility boundaries, not assumed Microsoft equivalence.
No new external services or real-tenant reference harness is required.

Implementation may refine internal code organization but must retain named cross-task
interfaces or record a coherent interface adjustment for all consumers. No endpoint or
client gate may be quietly converted to fixture-only behavior.

Plan review is the next action. Native is retained; approval starts inline execution.
