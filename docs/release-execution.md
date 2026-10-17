# M6 execution checkpoint

Branch feat/release-qualification in D:/projects/mockazure/.worktrees/release.
Base 0e637743bf64119a0e468bd6220385e6020157c0 (M5 PR9). Native implementation.
Tasks 1, 2 and 4 complete; Task3 final measurements and Task5 remain active.
No M6 final review has been dispatched. Exactly one fresh review remains required.

## Current work

The bounded maintenance implementation, scale generator/runner and reproducible
candidate builder are committed. Tuning at571578b passed small100/1000 and
medium5000/50000 with exact concurrent coverage and worker/API restarts.
Measured tuning is retained in release-scale.md/json.

Candidate97f594992a074bbeb74e26c07d24f3bf37117622 built identical wheel/sdist pairs,
and clean native/Docker package, capability and migration parity passed.
Strict artifact acceptance passed1test0.93s. That proof is historical after the
following release-compose readiness correction; rebuild before final acceptance.

Current-source qualification first found the preserved M2 port38443 conflict.
M6Graph now uses47443..47446, PostgreSQL55446. It then exposed PostgreSQL's
initialization-server health race: pg_isready over a Unix socket marked healthy
at13:10:57UTC, before TCP was available at13:11:06UTC. Migration failed and a
repeat after initialization passed. All five M6 compose health checks now require
-h127.0.0.1. RED regression confirmed the missing TCP guard;20affected tests pass.

Full unit/integration run started12:56UTC and continues in
work/release/full-regression.log. Its early unit failure was a stale test fixture
omitting build-requirements.txt; corrected fixture and metadata hash regression
pass5tests. Product API code did not change during that run.

Interrupted original-suite and scale reports are retained under
work/release/before-port-fix-* and before-health-fix-*.
Small61006 and61007 passed at older source fingerprints; medium62003/62004
interrupted, never counted as passing. Large63001 has not started.
Next fresh scale seeds: small61008, medium62005, large63001.

## Runtime ownership and authorization

User explicitly authorized stopping M2 and older MockAzure instances as needed.
Idle predecessor MockAzure containers have been stopped, with all containers and
volumes retained. VTW and unrelated containers were untouched. This supersedes
the earlier instruction to preserve running predecessor services, but does not
authorize deleting their data or changing their source.

Own testPostgreSQL55445 remains active. M6 suite ports:
Graph47443/PG55446, evidence40443/55447, security42443/55448,
mail44443/55449, ARM46443/55450. Native offsets+1/+2; managed identity+3.
All state is under .runtime/m6-*. Fresh own credentials and CA; no public tenants.

## Remaining sequence

Commit the health fix; rebuild the candidate; restart all current-source proofs.
Run original m3/m3b/m3c/m4 independently of scale. Run m5 original only after
scale ends because it shares the M6ARM runtime. Large remains100000users and
1000000records, with10800s outer and3600s post-import publication budgets.
Timeout, missing coverage or any failed mandatory case blocks release_ready.

Attach the actual three scale reports and candidate artifact manifest to the
five-suite aggregate. Run full regression, Ruff/diff, provenance and strict
acceptance. The four M4 collector-output failures remain mandatory blockers;
the M3B original MES audit JSON exception is the only waiver.

Dispatch exactly one fresh final reviewer with spec/plan/ledger and five Review
Focus items, then one consolidated regression-first fix pass. Rerun affected
proofs. Export decisions/review, commit/push, create and attach the M6 feature PR
stacked on M5 (draft if blocked). No automatic merge or registry publication.
Disable the existing overnight heartbeat only after all feasible work and
artifacts are delivered. No M7 is defined.

Ledger: .superpowers/sdd/2026-10-17-mockazure-release/progress.md.
