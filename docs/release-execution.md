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

Next scale seeds are small61009, medium62006 and large63002. Earlier completed
small/medium tuning is historical; failed/interrupted reports remain in
work/release/before-{port,health,review}-fix-* and individual profile journals.
The previous large63001 attempt failed during runtime setup before importing data.

Final source must be committed before candidate build and all current-source runs.
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
