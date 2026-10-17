# M6 execution and blocked release handoff

Recorded 2026-10-17T14:58:50.466354+00:00. Implementation/source candidate:
bf64b4e2ae09defdae83a6d306d777fc97bf6667.
Release fingerprint: 64e8af55c881ed05119a49c1740bb0827d6004536f6cdf36f7d1eee7dcd6c39e.
Subsequent commits contain documentation only.

Draft [PR10](https://github.com/joeavanzato/mockazure/pull/10) is stacked on
[PR9](https://github.com/joeavanzato/mockazure/pull/9). No merge or registry
publication occurred. The defined overnight implementation and measurement work
has reached handoff; release acceptance remains blocked. There is no defined M7.

## Actual current-source gates

| Gate | Outcome |
| --- | --- |
| M3 Graph | 14 runtime checks and three client profiles passed |
| M3B evidence | 25 runtime checks, three client profiles; 14 strict collector passes and six previously approved MES audit JSON exceptions |
| M3C security | 20 runtime checks, three client profiles and 20 collectors passed |
| M4 mail/Exchange | 19 runtime checks, three Graph profiles and real Exchange passed; 40/44 original collector cases passed |
| M5 ARM | passed |
| Small scale | 100 users/1,000 records; four exact readers and API/worker recovery passed |
| Medium scale | 5,000 users/50,000 records; four exact readers and API/worker recovery passed |
| Large scale | Failed during import at 100,000 users/979,000 acknowledged records |
| Candidate artifacts | Reproducible wheel/sdist and clean native/Docker package parity passed |

ARM passed 17 runtime checks, three SDK profiles and all eight original MES collector cases on the unchanged retry.

The final aggregate failed gates are: suite m4, scale large.
qualification_ready and release_ready are both false. The original strict
release acceptance test must remain red while these mandatory gates fail.
See [release-qualification.json](release-qualification.json).

The four M4 failures are Hawk inbox-rule JSON concatenation and MES trace CSV
fractional timestamp loss, each in application/delegated mode. No collector
rewrite, repaired output or new waiver was used. The prior M3B original MES
audit JSON limitation is the only approved exception.

## Large capacity failure and startup limitation

The large run's PostgreSQL WAL writer reported Cannot allocate memory,
aborted, and forced internal recovery; the importer received HTTP503.
No exact million-record collection or API-restart proof completed. The failed
report's published=0 is an initialized field, not a measured publication count.
The declared target remains 100,000 users/1,000,000 evidence records.
See [scale results](release-scale.md) and
[sanitized diagnostics](release-scale-failure.json).

Its private runtime is retained under .runtime/m6-large-63002-failed.
Final ARM used fresh owned state. Its first setup timed out with one worker
exited; a read-only fresh container verified valid schema and identity.
Recreating only owned containers recovered both workers and all1,003 Activity
fixture publications. The original suite was retried unchanged. Initial failed
evidence remains in work/release/arm-setup-failed-20261017T1441.
The exact suppressed startup exception remains unresolved; see
[recovery evidence](release-arm-recovery.json).

## Verification and review

The broad product run completed 755 passes and one stale ARM unit-fixture
failure. The fixture was corrected; all403 integration cases in that run passed.
A fresh complete unit run passed386 cases on the final harness. These are
separate runs, not a claimed single789-test run. Final report/provenance/restart
and artifact checks passed86 cases in15.11seconds. Ruff and diff validation
are part of the final handoff checks.

Exactly one fresh M6 reviewer found four important issues, all fixed with
regressions that first failed: full source attestation, four-reader restart
barrier, realistic suite budgets, and PostgreSQL readiness/shutdown budgets.
No second review and no deferred minor findings. See
[review](release-review.md), [decisions](release-decisions.md) and
[execution ledger](release-ledger.md).

## Artifacts and retained state

Candidate version0.1.0.dev0, all292 package files, migration037 and capabilities
match between clean wheel installation and Docker. Two clean wheel/sdist builds
have identical hashes. [Build evidence](release-build.json) includes source
objects, dependency inventories and exact artifact hashes. Docker layer
byte-identity and production Azure throughput are not claimed.

Safe exports under the chat's outputs/mockazure-m6-bf64b4e directory contain the
two archives, manifest, checksums and final reports. They exclude credentials,
private keys, environment files and runtime/database contents.

The user authorized stopping M2 and older MockAzure instances. Their containers
are stopped and data retained. VTW and unrelated services were untouched.
M6 qualification containers are stopped by their runner after completion.
No real tenant calls, host trust/DNS/firewall edits or predecessor source edits.

Tasks1,2 and4 completed their passing implementation contracts. Task3's measured
large gate and Task5's combined acceptance remain blocked; execution/handoff is
not a green release claim. The execution ledger is retained for this blocked
handoff. The overnight heartbeat is disabled only after reports, exports and
the draft PR are updated.

## Final strict gate result

Original ARM retry passed17runtime checks/3SDK profiles/8collectors in516.400s.
The strict combined release test failed as required: exactly suite m4 and
scale large remain blockers (one failed acceptance test,2.66s). Final
validation details are in release-validation.json. No passing release claim
is made. All source-bound proofs remain on fingerprint64e8af55c881ed05...

## Continuation shutdown

Overnight continuation continue-mockazure-m3c-through-m5 was set to PAUSED after final reports/exports and draft PR10 were updated. The idle M6 test database was stopped. No further milestone is queued; release blockers remain unchanged.
