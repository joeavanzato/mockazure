> Current stabilization results: [validated candidate and scope](stabilization-results.md). Earlier candidate outcomes below are retained as historical evidence.

# M6 scale measurements

Candidate bf64b4e2ae09defdae83a6d306d777fc97bf6667 was measured on Windows 10
with Docker Desktop, PostgreSQL 18 on an owned Windows bind mount, two API
replicas and two workers. Imports used the administrative HTTP APIs and normal
projection workers. Full reports, exact coverage digests and timings are in
[release-scale.json](release-scale.json); runtime limits are in
[release-scale-environment.json](release-scale-environment.json).

| Profile | Declared users / sign-ins | Outcome | Import and publication |
| --- | --- | --- | --- |
| Small, seed 61009 | 100 / 1,000 | Passed | 11.317 seconds; 88.363 records/second |
| Medium, seed 62006 | 5,000 / 50,000 | Passed | 141.616 seconds; 353.067 records/second |
| Large, seed 63002 | 100,000 / 1,000,000 | Failed during import | 100,000 users and 979,000 records acknowledged |

Small and medium each completed four concurrent exact-coverage readers, two per
resource family, with no read retries. Every reader held a continuation across
the same API restart. Worker interruption was included. Both medium evidence
readers collected all 50,000 records across 100 pages, in 239.519 and 240.110
seconds, with p95 page latency approximately 3.09 seconds. Counts, identifiers,
timestamps and duplicate/missing-record checks passed.

The large run started at 13:40:03 UTC and failed at 14:35:02 UTC on
2026-10-17. PostgreSQL's WAL writer reported "Cannot allocate memory" while
writing a WAL segment, aborted, and caused an internal database restart.
The import received HTTP 503 during recovery. The container's OOMKilled flag
was false; the underlying allocation failure has not been resolved. This is
a measured failure of the recorded runtime, not proof of a universal capacity
limit or a production Azure throughput claim.

The failed report's published=0 is an initialized value left before the
post-import sampling phase; it must not be interpreted as zero actual
publications. Exact large paging, publication cardinality and API-restart
coverage did not complete. The million-record target remains a mandatory
failed gate. No target reduction, output repair or waiver was applied.

Sanitized diagnostics are in [release-scale-failure.json](release-scale-failure.json).
The failed private runtime is retained locally under
.runtime/m6-large-63002-failed; it is not part of any artifact or export.
The independent final ARM qualification uses fresh owned state.

## Historical tuning

Historical reports in [release-scale-tuning.json](release-scale-tuning.json)
are separate from current candidate qualification. The baseline small run
published 1,000 records in 52.571 seconds (19.022/second). Baseline medium
imported 50,000 records, then exhausted its 300-second projection budget at
460 publications.

Measured job-selection and repair-scan costs justified bounded historical
sign-in publication batches of at most 100, source-key and active-queue
indexes, and repair scheduling at most once per five seconds per worker.
Each source retains its receipt, immutable publication and revision.
Generation checks, epoch fencing and rollback remain enforced; ordinary
dispatch runs before each batch. Regression tests cover worker death, lease
expiry, retry and generation isolation. Interrupted tuning attempts remain
non-success records and are not release evidence.

The recorded small/medium improvement does not establish success at the
million-record target. Further large-scale work should first reproduce and
resolve the WAL allocation failure, then rerun the complete declared target
and exact paging checks under documented resource and storage settings.
