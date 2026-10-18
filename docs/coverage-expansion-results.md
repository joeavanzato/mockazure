# Coverage expansion qualification — 2026-10-18

The combined collection workflow and full-size recovery profile passed locally.
These checks qualify the supported operations in [COVERAGE.md](../COVERAGE.md);
they do not certify complete Microsoft cloud or collector compatibility.

| Gate | Observed result |
| --- | --- |
| Combined original-client workflow | All ten stages passed: directory, Exchange, mail, SharePoint/OneDrive, Teams, Intune, Activity feed, methods, policies, two-tenant recovery. |
| Unit and final policy/CA/upgrade group | 534 passed. |
| Full integration run | 626 passed, 1 skipped; additional group-revocation/historical-policy targets subsequently passed 15/15. Final CI repeats the full suite. |
| Clean installation and storage guards | Fresh Docker initialization, HTTPS identity readiness, restart persistence, and missing-storage refusal passed in both combined and full-size runs. |
| Package candidate at `220f48d` | Two identical wheel/sdist builds; clean native install and Docker byte/capability/migration parity passed. Candidate only, not a published release. |
| Large collection recovery | 50,000 messages plus 50,000 drive items; each of eight concurrent readers returned its complete expected set after restarting both API instances. |
| Stream and mutation durability | 268,435,456 bytes matched SHA-256 after restart and range retry; the acknowledged message change persisted. |
| Tenant isolation | A second tenant with 101 records per family and overlapping public IDs returned its own exact sets; foreign mailbox access was denied. |

The Docker host reported 20 CPUs and 33,520,742,400 bytes of memory. Each reader
traversed 500 pages; observed read durations were 1062.69–1161.00
seconds, including the shared restart boundary and issued-cursor retry. Per-reader
95th-percentile page durations were 2.14–2.42 seconds. Post-run memory was
120.5 MiB and 115.8 MiB for the API replicas and 237.1 MiB for PostgreSQL. These
are measurements for this run, not peak-memory limits or throughput guarantees.
Representative PostgreSQL storage-query plans are retained in the scale report.

Evidence:

- [Combined stages, pinned client manifests and output hashes](qualification/2026-10-18-coverage-program.json)
- [Large-run exact identities, timings, resources, content hash and query plans](qualification/2026-10-18-coverage-scale.json)
- [Reproducible package candidate and artifact hashes](qualification/2026-10-18-coverage-package.json)
- [Reproduction commands and profile boundaries](coverage-program.md)

Native identity UUIDs are run-specific. Recovery evidence IDs and bytes use seeded
fixtures. Original client archives and extracted trees were checked before and after
execution. The combined runner uses a shared primary tenant across workload stages;
only mailbox/drive recovery is duplicated in the secondary tenant. Other workload
boundaries are covered by their PostgreSQL integration tests.

Original MES audit JSON and the other original-versus-patched collector distinctions
remain documented in [compatibility](compatibility.md). No output gate was silently
waived, and no tenant endpoint outside the local lab was used.
