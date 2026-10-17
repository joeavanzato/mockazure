# M6 execution checkpoint

Branch feat/release-qualification in D:/projects/mockazure/.worktrees/release.
Base0e637743bf64119a0e468bd6220385e6020157c0 (M5 PR9). Native implementation.
Tasks1,2,4 are complete. Task3 final measurement and Task5 handoff remain active.

## Consolidated review is finished

The sole M6 reviewer covered0e63774..04e263b and returned four important findings,
all confirmed and fixed in one regression-first pass. No second review.
See release-review.md and release-decisions.md.

- Shared fingerprints cover all packaged resource suffixes and native network guard.
  Artifact validation additionally checks currentHEAD and actual working input bytes.
- Every directory/evidence reader retains a first-page continuation across one
  synchronized API restart, with validated per-reader capture/resumption proof.
- Original-suite budgets use measured headroom:1800/5400/3600/5400/3600seconds.
- All six M6 databases require TCP readiness,180second startup grace and120second
  shutdown grace. The initial Graph socket readiness race and ARM~75second
  recovery are retained as failed-attempt evidence.

Full product regression completed755passed and one stale unit fixture failure in
1688.47seconds. That fixture omitted the new build lock and is fixed. Fresh final
complete unit regression passed386tests in14.17seconds. Product API code did not
change during review fixes. Earlier focused cleanup/batch/race tests passed.

## Fresh final qualification

Interrupted owned M6 runtime directories were moved, without deletion, into
.runtime/m6-attempts-before-final-review-fixes. M3B inspection confirmed5001live
seed users, including one leftover temporary user after interruption; final
qualification must use new clean instances, not weaken the expected5000count.

Final runs started2026-10-17T13:31UTC atbf64b4e2ae09defdae83a6d306d777fc97bf6667,
source fingerprint64e8af55c881ed05119a49c1740bb0827d6004536f6cdf36f7d1eee7dcd6c39e.
Scale seeds are small61009, medium62006 and large63002. Earlier completed
small/medium tuning is historical; failed/interrupted reports remain in
work/release/before-{port,health,review}-fix-* and individual profile journals.
The previous large63001 attempt failed during runtime setup before importing data.

Candidate build at that source completed: identical wheel/sdist pairs and native/Docker
package parity pass; strict artifact acceptance1passed0.61s. DraftPR10 is created and
attached: https://github.com/joeavanzato/mockazure/pull/10.
Active logs: work/release/final-original-suites.log and final-scale-{name}.log;
aggregates: work/release/qualification.json and final-scale-execution.json.
Run m3/m3b/m3c/m4 independently of scale; run originalm5 only after scale because
both own the same M6ARM runtime. The final large target remains100000users and
1000000records,10800second outer budget and3600second post-import publication
budget. Missing/partial/timeout results remain blockers.

M6 profile ports:
Graph47443/PG55446, evidence40443/55447, security42443/55448,
mail44443/55449, ARM46443/55450. Native offsets+1/+2, managed identity+3.
Own testPostgreSQL55445. Fresh credentials/CA; no real tenants or host trust/DNS edits.

## Acceptance and handoff

Rebuild wheel/sdist twice from committed Git bytes and verify native/Docker package
parity. Attach the actual three final scale reports and candidate artifact manifest
to the five-suite aggregate; run strict acceptance and provenance checks.
The four M4 original collector-output failures remain mandatory blockers.
The existing M3B original MES audit JSON exception is the sole waiver.

Publish measured reports and safe candidate artifacts, export the execution ledger,
commit/push and create/attach the M6 PR stacked on M5. Draft if blocked.
No registry publication, automatic merge, second review or M7.
Disable the existing overnight heartbeat only after all feasible defined work and
artifacts are delivered.

User explicitly authorized stopping old MockAzure services as needed. Idle
predecessor containers are stopped; source/data retained. VTW/unrelated containers
were untouched.

Ledger: .superpowers/sdd/2026-10-17-mockazure-release/progress.md.

## Measured progress at 2026-10-17 13:46 UTC

Final small61009 passed: 100 users/1,000 records, 11.317 seconds import/publication,
all four exact readers across restart, zero read retries.
Final medium62006 passed: 5,000 users/50,000 records, 141.616 seconds import/publication
(353.067 records/second). The two evidence readers each traversed100pages in
239.519/240.110seconds, p95approximately3.09seconds, zero retries.
Large63002 is running with all100,000users imported. Final exact evidence
coverage remains pending. Current-source M3 passed14runtime checks/3client profiles;
M3B is running. Do not treat these partial results as final release acceptance.

The verified candidate archives and manifest are exported under the projectless
outputs/mockazure-m6-bf64b4e directory; README explicitly labels acceptance blocked.

## First four current-source suites completed

M3 passed14runtime checks/3client profiles. M3B passed25runtime checks/3client
profiles and20collector acceptance cases (14strict;6existing MES audit JSON
exceptions), total2026.487seconds. M3C passed20runtime/3client profiles and
20/20collectors, total290.464seconds. M4 passed19runtime checks,3Graph profiles
and realExchange checks;40/44collectors passed. Its four original Hawk inbox-rule
JSON/MES trace precision failures remain mandatory blockers, total559.034seconds.

Completed small/medium and artifact proofs are attached and revalidated in
work/release/qualification.json; docs/release-qualification.json is its current
checkpoint. Remaining gates: suiteM4(blocked), suiteM5(pending after scale), and
large scale(running). Original-suite process10632 is finished. Scale23010 remains
active. Candidatebuild11333 is finished. Do not rerun completed current-source
suites without a source-changing fix.
