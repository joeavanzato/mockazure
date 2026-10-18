# Coverage expansion qualification — 2026-10-18

The final repaired runtime passed the combined collection workflow, full-size recovery,
and full local test suite. These checks qualify operations in [COVERAGE.md](../COVERAGE.md),
not complete Microsoft cloud or collector compatibility.

| Gate | Observed result |
| --- | --- |
| Full unit and PostgreSQL integration suite | 1153 passed, 1 skipped. |
| Independent review regressions | All 13 pass: concurrent membership/account/license revocation, linked device-code binding, code/proof expiry and both-replica readiness. |
| Combined original-client workflow | All ten stages passed: directory, Exchange, mail, SharePoint/OneDrive, Teams, Intune, Activity feed, methods, policies and two-tenant recovery. |
| Clean installation/storage | Fresh Docker setup, HTTPS readiness, persistent restart and missing-storage refusal passed. |
| Package candidate at `c418245` | Two identical wheel/sdist builds, clean native installation and Docker package/capability/migration parity passed. Not a published release. |
| Large recovery | 50,000 messages + 50,000 drive items; eight readers each verified their full expected collection after both API replicas restarted. |
| Content and writes | 268,435,456 streamed bytes matched SHA-256 and range retry; the acknowledged message change persisted. |
| Tenant isolation | Secondary tenant returned its own 101 records per family with overlapping public IDs; foreign mailbox access was denied. |

The Docker host reported 20 CPUs and 33,520,742,400
bytes of memory. Each reader traversed 500 pages. Observed read durations were
1062.09–1147.78 seconds, including restart/retry, and per-reader
95th-percentile page durations were 2.12–2.34 seconds. Post-run memory:
api-1: 113.8MiB; api-2: 132.7MiB; db-1: 243.7MiB. These are shared-host measurements, not peak-memory limits or throughput
guarantees. Regression and native qualification workloads also used this host.
Representative PostgreSQL query plans are retained in the scale report.

Evidence:

- [Combined stages, pinned client manifests and output hashes](qualification/2026-10-18-coverage-program.json)
- [Large-run IDs, timings, resources, content hash and query plans](qualification/2026-10-18-coverage-scale.json)
- [Reproducible package candidate and hashes](qualification/2026-10-18-coverage-package.json)
- [Reproduction commands and profile limits](coverage-program.md)

The independent review's four correctness findings were reproduced before repair.
Directory and licensing writes now share the issuance barrier, linked device-code
flows retain their trusted device binding, completed MFA proof remains valid through
code redemption, and recovery waits for both replicas. The full suite and original
client/large-run gates were repeated on that repaired runtime. No second review was used.

Native identity UUIDs are run-specific. Recovery IDs and bytes are seeded. Both runs'
393 runtime-source hashes match the qualified checkout bytes. The primary tenant hosts
all native stages; the secondary tenant covers mailbox/drive recovery. Other workload
boundaries are exercised by their integration tests.

Original MES audit JSON and the other original-versus-patched collector distinctions
remain in [compatibility](compatibility.md). No output gate was waived, and no tenant
endpoint outside the local lab was contacted.
