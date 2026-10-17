
# MockAzure M6 Release Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Native execution is already selected.

**Goal:** Finish bounded storage maintenance and produce measured, reproducible
release-candidate artifacts with current-byte qualification and honest blockers.

**Architecture:** Preserve the product's PostgreSQL state and actual API contracts.
Add bounded tenant maintenance, configure existing original-client harnesses for
fresh M6 profiles, then measure scale and package the committed candidate.

**Tech Stack:** Python 3.12+, PostgreSQL 18, SQLAlchemy, Alembic, pytest, Docker,
pinned original Microsoft SDKs, Az/Exchange, Hawk and MES.

**Spec:** docs/superpowers/specs/2026-10-17-mockazure-release-design.md

## Global Constraints
- Own D:/projects/mockazure only; no VTW, predecessor state, real tenants or system trust/DNS/firewall changes.
- Native implementation, one fresh final reviewer for M6, one consolidated fix pass.
- Fresh credentials/CA for own runtime; reuse only named manifest-verified public archives.
- Actual Graph v1.0/ARM/Exchange references; original collector commands and strict output oracles.
- Four M4 failures remain blockers. Only existing M3B MES audit JSON waiver is authorized.
- Small100/1000, medium5000/50000, large100000/1000000 user/evidence targets; publish measured outcomes.
- Commit/push feature branch and attach PR; user merges; no registry publication or M7.

## Review Focus
1. Cleanup racing first-page capture must not reclaim versions subsequently promised by a live cursor (Task1).
2. Expired leased UAL query retry must not resurrect deleted results or abandon a live evidence pin (Task1).
3. Qualification profile configuration must never touch predecessor runtimes or allow public-origin fallback (Task2).
4. Scale reporting must reject missing/duplicate records, partial profiles and interrupted runs as acceptance (Task3).
5. Candidate artifacts must not contain ignored/untracked secrets or claim reproducibility/current gates from stale bytes (Task4).

### Task 1: Bounded UAL and rule-history cleanup

**Files:** src/mockazure/{auditlog,mail}/cleanup.py; admin/maintenance_routes.py;
commands/maintenance.py; cli.py; admin/app.py; db/alembic/versions/035_maintenance_indexes.py;
tests/integration/test_maintenance.py; tests/unit/test_maintenance_cli.py.
**Interfaces:** cleanup_audit(session, tenant_id, now, limit) and
cleanup_rules(session, tenant_id, now, limit) return counters/remaining budget;
POST /_mock/tenants/{tenant_id}/maintenance/cleanup with max_rows1..1000(default500);
CLI maintenance cleanup --tenant-id --max-rows consumes same route.

- [ ] Write integration regressions for expired materialized rows, pending/running expiry fences,
  preserved 410 tombstone, live query pin, per-call deletion budget, tenant isolation,
  rollback, active mailbox page and concurrent first-page capture/mutation.
- [ ] Run tests and observe missing maintenance behavior.
- [ ] Implement local authenticated route/CLI and bounded cleanup. Lock EvidenceState before
  leased jobs; keep query/job tombstones. Reuse mailbox lock for page-capture and cleanup;
  exclude every version visible to a live exact-mailbox continuation. Add targeted indexes.
- [ ] Run maintenance plus existing audit-query/mail-rule/directory paging/job tests, migration
  empty/upgrade checks and CLI tests; require PASS with original behavior preserved.
- [ ] Commit feat: add bounded audit and rule history maintenance.

### Task 2: Current-candidate qualification profiles

**Files:** tools/qualification_profile.py; tools/{prepare,verify,qualify}_*.py;
tools/{mail,arm,security}_runtime.py; compose.release*.yaml; deploy/haproxy-release*.cfg;
integrations existing configuration boundaries; tests/unit/test_qualification_profile.py;
tests/acceptance/test_release_distribution.py; tools/verify_release.py.
**Interfaces:** QualificationProfile has owned runtime paths, compose names, ports, networks,
images and report paths. profile_for(stage) preserves default profiles and explicitly selects
M6-owned profiles. run_suites(output) records all suite outcomes even when a known gate fails.
validate_release(report) requires every mandatory case, current fingerprints and literal
booleans; returns readiness plus failures, never infers success from process exit alone.

- [ ] Write failing tests for profile path containment, distinct ports/project names, rejection
  of unknown/public origins, missing/stale report/case identities and retained M4 blockers.
- [ ] Implement explicit harness configuration with defaults preserved. Keep all predecessor
  checks and source/output hash validation; no replacement collector functions. Reuse existing
  code, parameterize paths/ports/images rather than cloning collectors.
- [ ] Initialize fresh own runtime(s), clean wheel environment and public vendor cache verified
  against pinned manifest. Check native loopback guard and container internal-network boundary.
- [ ] Run the current ARM original-client/collector profile to prove fresh runtime plumbing.
  Run allfive current-source directory/evidence/security/mail/ARM suites in Task5 after
  scale/artifact tooling stabilizes, retaining every output failure and every existing
  mutation/evidence, replica/restart,429/503 and lease check. This avoids stale intermediate
  attestations; it does not remove any mandatory suite.
- [ ] Commit feat: qualify release candidates in isolated runtime profiles.

### Task 3: Deterministic scale and recovery qualification

**Files:** tools/scale_dataset.py; tools/benchmark_release.py; tests/unit/test_scale_dataset.py;
tests/integration/test_scale_recovery.py; docs/release-scale.json.
**Interfaces:** iter_directory_batches(seed, users, batch_size) and
iter_evidence_batches(seed, users, records, batch_size) yield existing validated public dataset
envelopes with bounded memory and deterministic identities. run_profile(profile, config, report)
imports through actual local APIs, waits for normal projection, traverses pages and records
counts/digests/timings/errors with passed=false until every assertion succeeds.

- [ ] Write failing tests for deterministic unique counts/identity streams, valid schema,
  bounded batches, invalid profile sizes and interrupted/partial report rejection.
- [ ] Implement generators and measured runner; include import/publication/read elapsed time,
  page latency percentiles, database/runtime configuration and exact identity checks.
- [ ] Run small100/1000 then medium5000/50000 with concurrent callers and API/worker restart.
  Diagnose measured bottlenecks before changing code.
- [ ] If needed, add regression-first bounded projection optimization in evidence/jobs only:
  preserve per-source receipts, immutable publications, generation and lease fencing, atomic
  rollback and replays. Document measured reason and compare unchanged API/collector outputs.
- [ ] Run large100000/1000000 without lowering declared targets. Record actual achieved counts,
  timings and resource limits; failure remains a blocker. Complete exact page/digest checks
  with restart and no duplicate publication. Commit scale runner and measured report.

### Task 4: Reproducible candidate artifacts and parity

**Files:** tools/build_release.py; tests/unit/test_release_artifacts.py;
tests/acceptance/test_release_artifacts.py; docs/release-build.md; pyproject.toml as needed.
**Interfaces:** build_candidate(source_commit, output_dir) builds wheel/sdist from allowlisted
tracked bytes using pinned tools and SOURCE_DATE_EPOCH, produces manifest/dependency inventory,
and verifies members. Two clean builds must compare hashes. No automatic registry push.

- [ ] Write failing tests for excluded runtime/env/private-key/untracked files, traversal or
  duplicate archive members, mismatched manifests, missing licence/resources and stale gates.
- [ ] Build twice from the exact source; validate wheel/sdist member bytes and SHA256 equality.
  Fix nondeterminism only with reproducible build configuration, never by editing finished
  artifacts presented as original builds.
- [ ] Clean-install wheel and exercise module/console CLI, migration head, capabilities and
  packaged resource hashes; compare Docker installed package and actual API/client behavior.
- [ ] Produce candidate manifest, provenance, dependency inventory, reproduction commands,
  actual scale/gate reports and explicit blocked-release status when mandatory gates fail.
- [ ] Commit build support and safe artifact metadata; retain binary deliverables locally.

### Task 5: Full regression, final review and handoff

**Files:** README.md; docs/{capabilities,compatibility,release-execution,release-decisions,
release-review,overnight-execution}.md; spec/plan; release reports.
**Interfaces:** Consumes current-source gates and artifacts; produces bounded final status,
attached feature PR stacked on M5 and disabled overnight continuation after work is finished.

- [ ] Run allfive current-source directory/evidence/security/mail/ARM original-client suites
  using the Task2 profile runner, retaining all actual collector failures. Run full
  unit/integration suite, Ruff/diff, schema/archive provenance and current-byte candidate
  acceptance; rerun affected gates after any source-changing fix.
- [ ] Dispatch exactly one fresh final reviewer with spec/plan/ledger/range and five Review
  Focus items. Regrade, fix Important/Critical in one RED-to-GREEN pass, record every ruling.
- [ ] Update actual capabilities, unsupported behaviors, failure reproductions and measured
  sizes; no fabricated all-green acceptance. Export ledger, commit/push and create/attach PR,
  draft if mandatory release gates remain unresolved.
- [ ] Verify reproducible candidate references, leave predecessor state intact and disable
  the existing overnight heartbeat once all feasible defined work/artifacts are delivered.

## Plan self-review
The spec maps cleanup to Task1, fresh combined qualification to Task2, all profile
sizes/recovery to Task3, reproducibility/parity to Task4 and honest handoff to Task5.
Task1 interfaces are consumed through the CLI/HTTP in later parity tests; Task2 owns
all local profile configuration used by Tasks3/4. Source changes invalidate report
fingerprints, so final qualification follows the final product/harness commit.
Each Review Focus item has a corresponding regression step. Native execution and
adoption of reasonable decisions are already authorized; no approval pause.
