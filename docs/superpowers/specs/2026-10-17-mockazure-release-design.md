
# MockAzure M6 release qualification design

Adopted under the user's instruction to finish all defined milestones without
routine approval pauses, using reasonable recorded decisions and Native execution.
This is the final defined milestone. No M7, package publication or automatic merge.

## Outcome and boundaries

Qualify the same PostgreSQL-backed emulator across a clean pip installation, CLI,
Docker replicas and original local Microsoft clients/collectors. Publish measured
scale and reproducible candidate artifacts with an honest gate ledger. An artifact
may be prepared while acceptance is blocked; release_ready must remain false when
a mandatory gate fails. The four M4 failures (Hawk multi-rule JSON and MES trace
fractional precision, application/delegated) remain mandatory. The sole exception
is the previously approved M3B original MES audit JSON defect.

No changes to VTW, predecessor worktrees/runtimes, real tenants, system trust,
DNS or firewall. Fresh runtime credentials and CA, no copied secret/untracked files.
Named public archives may be reused only after the pinned manifest verifies them.
Use existing actual Graph v1.0, ARM and Exchange references and original collector
source. No fake downloaded commands, output repair or shaped one-rule fixtures.

## Alternatives and adopted architecture

Merely aggregate old reports is insufficient because later code can regress earlier
surfaces. Reimplementing collector harnesses risks weakening their oracles. Adopt
explicit runtime configuration for the existing qualification flows and rerun the
selected profiles against the current candidate bytes in M6-owned environments.
Keep the prior default profiles available for reproduction; no runtime mutation
of predecessor configuration. Configuration selects local paths, names, ports and
images; it must not bypass authorization, alter original commands or skip checks.

Product work is bounded storage maintenance. Qualification work comprises shared
profile plumbing, deterministic scale inputs, measured runs and candidate packaging.
Optimize only after measurements identify a bottleneck and preserve the same
public import, source provenance, generation, publication and job-lease guarantees.

## Storage maintenance

Add authenticated local administrative cleanup and matching CLI. Default maximum
500 deleted rows, explicit integer 1..1000. Tenant-scoped and transactionally bounded.
A result reports deleted counts and remaining eligibility without exposing secrets.

Expired UAL materialized query items may be deleted in batches. Preserve query/job
tombstones so authenticated clients still receive the established 410 expiry result.
An expired pending/running query must be fenced failed before cleanup: clear lease,
increment epoch, release its evidence pin under the existing EvidenceState-to-Job
lock order. Never delete live results or pins for unexpired queries. Rollback is
atomic if deletion fails. Do not cascade-delete a query containing an unbounded
number of result rows.

Prune only closed MailRuleVersion rows invisible to every live matching mailbox
continuation. Current rules and versions remain. Acquire the same mailbox lock for
first-page snapshot/cursor creation, mutation and cleanup, so a cursor cannot be
issued after its history was reclaimed. Exact resource and tenant binding; another
mailbox's revision is not a pin. Handle replay/expiry and concurrent cleanup safely.
Expired directory continuations may be reclaimed in a bounded share of the budget.
Do not delete immutable source receipts or pending provenance to save space.

## Qualification environments

Separate own M6 namespaces for selected evidence/security/mail/ARM suites, with
fresh credentials and isolated Docker networks. Two API and two worker processes
share PostgreSQL for runtime checks. Native caller traffic is loopback-only with
the existing audit guard; container clients use internal networks with blocked DNS.
Require exact configured origins and redirects disabled at transport boundaries.
Port/network availability is verified before initialization.

Existing predecessor reports are historical references only. New report identity
binds source commit and package bytes, lock, official schema manifests, original
archive hashes, images, profile and output hashes. An interrupted/failed run writes
non-success early; missing cases never become passes. Keep all actual M4 failures
visible in the combined report and keep running independent suites.

Package parity includes clean wheel install, module/console CLI, migration head,
capabilities and packaged resources compared with Docker; original SDK/API probes
and network boundaries exercise both. Combined mutation-to-evidence checks verify
Graph, mail/UAL and ARM behavior on the candidate.

## Scale

Profiles: small 100 users/1000 evidence records; medium 5000 users/50000 evidence;
large 100000 users/1000000 evidence. Deterministic UUIDs, fixed public timestamps,
seed and provenance. Stream bounded supported imports rather than a giant in-memory
request. Use real API import and normal projection workers, never direct table
fabrication presented as end-to-end performance.

Measure input generation/import/publication/full-page collection times, exact counts
and identifier digest, duplicates/missing records, latency percentiles, failures,
database size and runtime resource configuration. Directory and evidence paging
must be checked across replica restart; include concurrent collection and worker
interruption without duplicate publication. Record time budgets and achieved sizes
if the target fails; do not relabel a smaller case as the million-record target.

A bounded projection optimization is allowed only if measured single-record work
cannot meet practical qualification time. It must retain per-source receipts,
immutable publication ordering and atomic epoch-fenced completion; regressions for
crash, lease expiry, tenant/generation separation and retries precede changes.
No promises of production Azure throughput or arbitrary scale.

## Candidate artifacts

Build wheel and source distribution twice from the same committed source using
pinned build tools and SOURCE_DATE_EPOCH. Compare hashes, or explicitly document
any unreproducible part as a blocker. Prepare SHA256 manifest, build instructions,
licence/NOTICE, dependency inventory and measured qualification report. Include
only allowlisted tracked source/resources; exclude runtime, private keys, env files,
tokens, work logs, local database contents and untracked files. Validate archive
members and secret-shaped files without printing their contents.

Docker artifact metadata binds exact runtime/base image IDs. A locally reproducible
build recipe is required; no claim of byte-identical Docker layers unless measured.
No push to a package/container registry. Candidate version/status must not imply
acceptance. Final feature PR is stacked on M5; user merges.

## Verification and completion

Unit/integration tests cover cleanup budgets, active pins, expired leases, tenant
isolation, cursor races and failure atomicity; migrate empty and predecessor DBs.
Run full product regression, current-byte original clients/collectors, restart/
fault/lease probes, all three scale profiles and artifact checks. One fresh final
reviewer, one consolidated regression-first fix pass; no repeated reviews.

Completion records each gate as pass, fail, waived-with-existing-authority or
not-run, with evidence. Missing mandatory evidence blocks release_ready. Preserve
outstanding upstream blockers with concrete repro and inputs; do not wait for the
user to merge before preparing independent artifacts. Finish all feasible M6 work,
create/attach its PR, then disable the overnight continuation.
