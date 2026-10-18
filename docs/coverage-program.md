# Reproducing the coverage program

All commands operate on fresh local Docker labs and use loopback HTTPS. Generated
state lives under `work/default-smoke-*`; reports and service logs are retained.
Completed qualification removes only its unique containers/networks, preserving the
PostgreSQL volume and instance files. No command in this profile contacts a tenant.

Install the locked development environment and prepare the pinned clients with
`tools/prepare_clients.py`. The manifests and vendor directories are listed in
`tools/verify_coverage_program.py`; `--verify-only` checks both downloaded archive
hashes and extracted file trees before and after qualification.

- `python -m tools.verify_coverage_program`: original Graph SDK/PowerShell directory,
  original Exchange commands and MES transport CSV, mailbox MIME/attachments/delta,
  site/drive content/delta, Teams, Intune, Activity feed, method management/reset and
  method policies, followed by two-tenant recovery. Each stage must pass. Generated
  reports record pinned versions, manifest hashes and output hashes. Runtime-created
  identities remain run-specific; the recovery dataset uses deterministic record IDs
  and bytes, and validates exact counts and identities independently of paging order.
- `python -m tools.verify_authentication_policies`: focused original-client policy
  qualification and actual HTTPS login/consent/MFA/refresh behavior.
- `python -m tools.benchmark_coverage --small`: CI recovery profile, 404 primary
  message/item metadata records, 2 MiB streamed object, eight readers and two API
  replicas. A second tenant has 101 records per collection with overlapping public
  IDs, to check isolation and foreign mailbox denial.
- `python -m tools.benchmark_coverage`: 100,000 primary records (50,000 messages and
  50,000 drive items), 256 MiB streamed object, the same eight-reader replica/restart
  boundary and second tenant. Every reader must return the exact expected identities.
  The streamed download and range retry must match deterministic bytes; a previously
  acknowledged message mutation must survive restart.

The generated proxy uses two backend slots only for the recovery lab. All eight
readers pause with issued continuations before restarting both API replicas, then retry
and resume those continuations. Record/page timings, Docker CPU/memory configuration,
post-run container usage and representative PostgreSQL storage-query plans are retained.
These are measurements on the observed hardware, not Microsoft service SLAs or a peak
memory certification. Content is generated and hashed in one-MiB blocks.

The small and policy profiles are CI jobs alongside the existing workload checks.
The full local result, combined original-client result, unit/integration regressions,
clean install and package parity report are separate gates. Passing one does not waive
another. The existing original MES audit JSON defect and all original-versus-patched
collector labels remain in [compatibility](compatibility.md).

[Observed qualification results](coverage-expansion-results.md) include the combined
original-client run and the full-size local recovery measurements.
