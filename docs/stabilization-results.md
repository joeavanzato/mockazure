# Post-M6 stabilization results

Candidate: 8c7287c381c33b2e7e2f53e756305cdb6b27d830. PR: https://github.com/joeavanzato/mockazure/pull/11.
Source fingerprint: c2b79a72bf82b1e56d2a6b49bd2f1c71cfabdbd92efab530e30a73e0b7a5a7ab.

All required release gates passed for the explicitly qualified client profile.
This is a local development candidate (0.1.0.dev0), not a registry publication.

- Runtime: release PostgreSQL uses durable Docker-managed Linux volumes. ARM
  startup, API/PostgreSQL/worker restart checks and all eight original MES cases passed.
- Collectors: all 44 mail cases passed with the explicit serialization-v1 source
  patches. The eight affected original baseline cases remain separate: four known
  serializer failures and four permission-denial passes. The original archives remain
  unchanged. No emitted artifact was repaired and no fixture or precision gate reduced.
- Qualification: M3, M3B, M3C, M4 and M5 passed, with the previously approved M3B
  original MES audit-JSON limitation retained. Reproducibility, native/Docker parity
  and both final release acceptance tests passed.

The broad run passed 811 tests, including 403 PostgreSQL integration cases.
After qualification-only corrections, frozen-source unit and serializer checks
passed 417 tests (411 unit and six native PowerShell serializer cases).

## Scale

| Profile | Users / records | Published | Pipeline seconds | Records/second |
| --- | --- | --- | --- | --- |
| small | 100 / 1,000 | 1,000 | 12.868 | 77.709 |
| medium | 5,000 / 50,000 | 50,000 | 170.822 | 292.702 |
| large | 100,000 / 1,000,000 | 1,000,000 | 2258.159 | 442.839 |

The two large sign-in readers each collected all 1,000,000 records across
2,000 pages. Their read durations were 2254.747 and 2251.628 seconds;
p95 request latency was 2.252 and 2.245 seconds, with 0 and 0 retries.

Measurements use compose.release.arm.yaml with two API replicas, two workers
and durable Docker-managed PostgreSQL storage. Profiles ran sequentially in
separate tenants on the same runtime, retaining earlier profile data.
Each profile completed four concurrent exact-coverage readers, two per resource
family, across an API restart; worker interruption was also included. Counts,
identities, timestamps and duplicate/missing-record checks remained mandatory.
These are local measurements, not a throughput SLA or Microsoft service equivalence.

The earlier Windows-bind WAL allocation failure was not reproduced in the bounded
storage probe. The Linux volume probe was 3.44 times faster, and the complete
million-record qualification now passed on that configuration. This establishes
the measured configuration's result; it does not prove the low-level cause of
the earlier PostgreSQL allocation error.

A first stabilization attempt exposed a qualification readiness race: an audit
query correctly pinned 938 published records while import of 1,003 had already
been acknowledged. The qualifier now waits through Graph until all expected IDs
are published before pinning the restart-test snapshot. API behavior was unchanged.
See stabilization-readiness.json and stabilization-execution.md.

Historical failed runtimes, original baseline artifacts and unsuccessful
qualification/build attempts are retained locally. Runtime data, keys, credentials
and environment files are excluded from public artifacts. VTW was untouched.

Detailed evidence: stabilization-validation.json, stabilization-qualification.json,
stabilization-scale.json and stabilization-build.json. Source-patch details:
../integrations/collector-patches/README.md.
