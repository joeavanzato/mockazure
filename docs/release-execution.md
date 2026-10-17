# M6 execution checkpoint

Worktree: D:/projects/mockazure/.worktrees/release.
Branch: feat/release-qualification. Current implementation is committed and pushed
through 6c9e2f4. The M6 branch starts at reviewed M5 commit 0e63774.

Tasks 1, 2 and 4 are complete. Task 3 remains open for final measurements of all
three declared sizes. Task 5 final regression and original-client qualification
are running. No M6 final reviewer has been dispatched yet.

## Completed work

Bounded maintenance preserves live evidence/rule pages and expired-query 410
tombstones while reclaiming obsolete bulk data. Expired leases are fenced in the
same transaction as query cleanup. The owned test PostgreSQL uses port 55445.

Fresh M6 profiles isolate all five original-client suites from predecessor state.
The initial ARM profile proved 17 runtime gates, three SDK profiles and eight MES
cases on source d339453. That proof is historical, not final release acceptance.

Scale tuning introduced bounded historical sign-in publication, individual
source revisions/receipts, atomic generation/epoch fences, periodic missing-job
repair and indexed active-job selection. The final tuning source 571578b passed
both small and medium profiles with exact concurrent collection and restarts:
small 100 users/1,000 records in 8.863 seconds including worker restart; medium
5,000 users/50,000 records in 186.570 seconds for import/publication. Both medium
sign-in readers retrieved all 50,000 identities across 100 pages in about 161
seconds. Baseline failures and interrupted experiments remain documented in
release-scale.md and retained under work/release.

Candidate source 54603c0368441259a6b531e6e0452506b8afae3e built identical wheel
and source-archive pairs. Clean native installation and Docker match every
package file, migration head 037 and the capability document. Module/console
entry points pass. Artifact acceptance plus its unit tests passed 19 tests in
3.36 seconds. The later commit only renamed a colliding acceptance test module.

The current source fingerprint is:
4bc204af4141bdd7e7a7c098a0ede0a844926ef28f53f07fb7fb76e33d9bf3b9

## Active qualification

Three independent processes started at approximately 2026-10-17 12:56 UTC:

- Full tests/unit and tests/integration against the owned test PostgreSQL.
  Log: work/release/full-regression.log.
- Current directory, evidence, security and mail original-client suites.
  Log: work/release/final-original-suites.log. Aggregate: work/release/qualification.json.
- Final small seed 61006, medium seed 62003 and large seed 63001 measurements.
  Logs: work/release/final-scale-{small,medium,large}.log.
  Execution journal: work/release/final-scale-execution.json.
  Actual profile reports: work/release/scale-{name}-{seed}.json.

The large target remains 100,000 users and 1,000,000 records. It has a three-hour
outer budget and one-hour post-import publication budget. A timeout or incomplete
traversal remains a failed gate; the target is never reduced.

ARM original-client qualification must run after scale because both own the M6 ARM
runtime. Attach the final artifact and all three scale reports to the combined
report, then run strict acceptance. Source-changing fixes invalidate those proofs.

## Remaining handoff

Run exactly one fresh final M6 reviewer after the final evidence is available,
make one consolidated regression-first fix pass, and rerun affected/current-source
gates. Export decisions/review and create an attached feature PR stacked on M5,
draft if mandatory gates remain unresolved. No automatic merge or registry push.

The four M4 original collector-output failures remain mandatory release blockers.
The existing M3B MES audit JSON exception is the only waiver. Preserve predecessor
runtimes and all failed evidence. Never touch VTW, real tenants or host trust/DNS/
firewall settings. Disable the existing overnight heartbeat only after all feasible
defined work and artifacts are delivered.

Execution ledger: .superpowers/sdd/2026-10-17-mockazure-release/progress.md.
