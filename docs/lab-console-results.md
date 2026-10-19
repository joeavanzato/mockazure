# Lab console and population qualification

This report covers the service browser, stacked profiles, population runner and
deployment changes. Start with [the deployment guide](getting-started.md) and
[profile authoring](profiles.md). Public API breadth remains documented separately
in [COVERAGE.md](../COVERAGE.md).

## Tested source and distributions

Runtime source: `ee04446d757accb75dbd31a14c5aec8baa917036`.
Qualification runner: `5a28c2b6128aaad963a2c8b75177e78b2e66139b`.
The subsequent results/documentation commit does not change the tested runtime.
Canonical package fingerprint:
`ab2489e8c87121600294bbc3dab58d0f05b46e3beee0e13a88e5e8f39e4cc9ab`.

Two builds from allowlisted Git object exports produced identical wheel and source
archives. Archive inspection checked the exact member set and committed bytes:
1,157 package files and 1,165 source files. Isolated wheel and sdist installations
without repository imports, plus a network-disabled Docker probe, resolved all
four templates, served all eight console assets with security headers, and rejected
unauthenticated management access. All three package fingerprints matched.

| Artifact | SHA-256 |
| --- | --- |
| Wheel | `035776e0ed42190f618d064b3105e35a470762886073b0acb2c18cdab49dd4b1` |
| Source archive | `ad35181ee2b352cf43dc3f8c2a2481374aec2ce08aaee1fefa078a50c6795916` |

## Local workflow results

| Template | Completed steps | Public users | Public sign-ins | Replay creates | Final recheck seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| `developer-lab` | 20 | 20 | 100 | 0 | 15.125 |
| `collection-lab` | 41 | 20 | 100 | 0 | 50.687 |
| `investigation-lab` | 42 | 20 | 120 | 0 | 39.312 |
| `volume-lab` | 5 | 10,000 | 100,000 | 0 | 847.500 |

Native workflows used verified HTTPS on localhost with PostgreSQL, an API process
and a separate worker. The volume creation run was interrupted after its first
100 created records, resumed, and finished with 110,003 verified operations.
Public pagination independently counted 10,000 users in 11 pages and 100,000
sign-ins in 101 pages. Replays use the same namespace and frozen reference time.

These datasets were initially created during implementation. The final checks
replayed and read them through the final runtime; the reports retain prior
provenance. This is not a claim that every dataset was freshly created by the
final commit. Creation and concurrency regressions additionally run against
fresh test databases.

All 85 catalog families passed bounded browsing. Template steps exercised 40 distinct families. The browse matrix includes 62 empty families in its selected developer tenant; those are browse checks, not populated-data or all-operation assertions. Unknown families returned a useful 404.

Docker additionally completed the collection template and a duplicate-free replay
through its published HTTPS endpoints. Local Docker qualification used two owned
containers on the existing bridge with explicit API/database links because this
host's Docker address pools were exhausted. The committed CI job exercises fresh
default Compose creation. Local bridge evidence alone does not certify that CI
path. No real Microsoft tenant was contacted.

## Regression and browser checks

| Gate | Passed | Skipped | Seconds |
| --- | ---: | ---: | ---: |
| `final-unit` | 803 | 0 | 151.72 |
| `final-shard-1` | 385 | 0 | 2,516.57 |
| `final-shard-2` | 329 | 0 | 2,342.43 |
| `final-shard-3` | 307 | 0 | 2,511.97 |
| `final-acceptance` | 6 | 0 | 35.33 |

The complete unit/integration suite passed 1,824 tests, partitioned by disjoint integration files. `MOCKAZURE_LARGE_CONTENT_TEST=1` and `MOCKAZURE_NATIVE_STRUCTURED=1` enabled the 256 MiB content and native structured-client opt-ins. Ruff lint, whitespace checks and the coverage consistency check passed (317 operations across six public service groups).

Earlier diagnostic runs are not included in these final counts. Their failures and the Windows test-harness correction are retained in the execution record; the final complete suite is the release evidence.

Browser qualification exercised password login, bounded resource pages, profile
composition and saved-bundle reopening, preview/apply, run progress, invalid input,
inert HTML values, adding a directory user, and public Graph readback. Screenshots
were checked at desktop and narrower widths. The browser bridge pinned requests
to the local origin and validated its CA; certificate validation was not disabled.
Separate two-replica checks established access through the configured shared hostname and
immediate logout/session invalidation across replicas.

The independent final review found three Important issues and one issue initially
graded Minor. All four were addressed: serializable fixture creation, locked
public-write preconditions, bounded workload-edit validation, and reopening saved
bundles. Each had a failing regression before the fix. The saved-bundle issue was
treated as Important because reusing compositions is a core workflow. There are
no deferred minor findings from that review. Earlier cloud changes were outside
this focused review range and retain their existing evidence.

Hosted run [39341646636](https://github.com/joeavanzato/mockazure/actions/runs/39341646636)
for the qualification commit did not execute any job steps. GitHub rejected job
startup because recent account payments failed or the spending limit needs to be
increased. Hosted CI is blocked, not passing; the repository owner must resolve
that account setting and rerun it. The additional Ubuntu runner-image migration
notice is informational, not the failure cause.

## Operational limits

- One shared administrator password grants management access; it is not a public
  Graph/Storage token or a per-user authorization system. Public operations still
  require local identities, permissions, licences and service roles.
- Read-only catalog families remain read-only when no safe additive adapter exists.
  The optional inspector exposes a curated scalar view, not arbitrary SQL.
- Workload editing validates at most 10,000 existing-plus-submitted records and
  20,000 relationships per scope. Larger scopes remain paginated and can use the
  explicit bulk-import interfaces. Conservative counts can reject a replacement
  near the limit.
- Population fixtures use serializable transactions and return conflicts rather
  than blindly retrying competing writes. Local `X-MockAzure` precondition headers
  fence population writes; they are documented emulator extensions, not Microsoft
  API parity claims. Ordinary requests without them retain their normal behavior.
- Durable population receipts govern automatic replay. Standalone resource POSTs
  do not provide exactly-once delivery after an uncertain response; inspect before
  retrying. There is no automatic rollback or namespace-wide deletion.
- The qualification tool retries only an explicit `409 PreviewStale`, with at most
  five newly generated previews. Other conflicts and uncertain writes still fail.
- Starter profiles explicitly provision the existing Entra P2 SKU. Narrower
  entitlement tests should override that baseline. Implicit reference time stays
  fixed within a dataset namespace.
- During the initial native volume run, observed API/worker peak working sets were
  approximately 172.5/139.6 MiB. That run took approximately 2,088 seconds and
  included implementation-time restarts; these observations are not throughput
  guarantees, clean-start benchmarks, or measurements of every final-code path.
- No guest compute execution, real mail delivery, proprietary detection engine,
  SMB or NFS is provided. Browse success does not certify every operation in a
  family, every Microsoft schema variant, or an arbitrary external collector.

## Reproduce

Use a disposable instance initialized as described in the deployment guide, then:

```powershell
uv run python -m tools.verify_lab_console --config .runtime/instance/native.json --profiles developer-lab collection-lab investigation-lab volume-lab --report work/lab-console.json
$env:MOCKAZURE_LAB_REPORT = "$PWD/work/lab-console.json"
uv run python -m pytest tests/unit/test_lab_console_package.py tests/acceptance/test_lab_console_workflows.py -q
```

For browser qualification, configure the disposable shared password from a
protected `browser-test.password` in that instance, install Playwright Chromium,
and set `MOCKAZURE_BROWSER_CONFIG` and `MOCKAZURE_BROWSER_TENANT` before running
`tests/browser/test_lab_console.py`. The CI job shows the automated sequence.

Retain generated JSON reports and test logs as execution evidence. A saved report
must match the current package fingerprint and exact expected cases; the acceptance
test rejects missing profiles, wrong counts, duplicate-producing replays, unknown
steps and incomplete family coverage.
