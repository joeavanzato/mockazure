# Cloud workflow qualification

Phases A-F have individual implementation and native-client evidence. Phase G
binds the combined native, scale and distribution checks to one source identity.
The generated reports below are authoritative for a particular run; this static
document is not a substitute for their current-commit validation. The phase G PR
records the final tested commit and measured results, with the corresponding
report artifacts. A missing, stale, skipped or failed required gate is a failure.

## Required combined evidence

| Gate | Command / evidence | Requirement |
| --- | --- | --- |
| Native clients | `python -m tools.verify_cloud_workflows --phase all --ecosystems python powershell dotnet typescript --report work/cloud/all.json` | All six phases, four declared ecosystem cells, pinned versions, one commit and fingerprint; explicit not-applicable and REST-fallback cells remain visible. |
| Cloud scale | `python -m tools.benchmark_cloud_workflows --records 100000 --binary-bytes 268435456 --readers 8 --report work/cloud/scale.json` | Exact IDs, 256 MiB hash, eight readers, two API replicas/restarts, 32 acknowledged concurrent writes, tenant isolation, queue contention, file ranges, diagnostics and request/failure counters. |
| Coverage | `python -m tools.check_cloud_coverage --check --report work/cloud/all.json` | Explicit operation declarations, current contracts, implementation and behavior references, offline Microsoft source closure. |
| Candidate artifacts | `python -m tools.build_release --commit HEAD_SHA --output work/release/candidate` | Committed source bytes, reproducible wheel/sdist and Docker package parity. |
| Installed behavior | `python -m tools.verify_cloud_distribution` | Six A-F behavior tests in each installed wheel, sdist and Docker image; real JUnit cases and packaged reference closure. |
| Existing release gates | `python -m tools.verify_release` with backing report arguments | Existing collector suites, three original scale profiles, candidate parity, plus all four cloud gates. |

The coverage table distinguishes implementation from qualification. A route alone
cannot establish support, and a missing native-client report cannot be treated as
a pass. Original MES audit JSON concatenation remains an explicitly accepted
upstream limitation; the existing API, SDK, Hawk and MES SOF-ELK gates remain.

The cloud dataset is 100 inventory accounts, 1,000 native Blob diagnostic records
and 98,900 Table entities. Infrastructure, isolation sentinels and concurrent
writes are separately measured. All mutations use local public APIs or SDKs.
Lifecycle, migration, permission changes, webhook downtime, stale workers and
429 behavior are covered by the full regression and native phase suites rather
than duplicating every scenario inside the concurrent scale loop.

GitHub Actions was unable to start phase F jobs because the account's payments
or spending limit blocked execution. Local results are separate evidence and
must not be described as passing GitHub CI. The billing setting requires the
repository owner's attention.

## Reading the measurements

`sourceCommit` and `sourceFingerprint` identify the exact native/scale/distribution
inputs. Scale reports include expected and observed counts, each reader's ID-set
hashes, uploaded/downloaded content hashes, restart markers, acknowledged writes,
database size and API process memory checkpoints. Kernel `VmHWM` captures transient
peaks, including the public HTTP upload, and checkpoints immediately before each
restart preserve every process generation. Each API process must stay within
256 MiB, and upload/download high-water growth must stay within 64 MiB. The
256 MiB upload uses a single native SDK Put Blob request. These are local test
allowances, not production sizing guidance. PID 1 owns all API listeners; helper,
worker and PostgreSQL processes are outside this API memory allowance.

`requests` counts workload REST and SDK/MSAL HTTP transport attempts, response
status codes and transport exceptions. It excludes lab bootstrap/readiness probes
and Docker/database observation commands. Expected permission-denial responses
remain in the HTTP-error count; the workflow assertions distinguish those from
unexpected failures. A passing scale gate requires zero transport exceptions and
zero server-error responses. Counters retain no URLs, credentials or bodies.

The default regression run skips four explicit opt-in tests. Run them with
`MOCKAZURE_LARGE_CONTENT_TEST=1` and `MOCKAZURE_NATIVE_STRUCTURED=1`, after
`python -m tools.prepare_structured_clients`, using
`tests/integration/test_content_storage.py::test_256_mib_content_uses_bounded_chunks`
and `tests/integration/test_structured_original_clients.py`. These checks remain
additional to the six-phase native matrix and full cloud scale gate.

## Harness validation before the combined gate

At `f76b5153f4799bda583f8b3c748d7955b96e134b`, the reproducible candidate and
installed wheel/sdist/Docker workflow checks passed: six representative A-F
behaviors in each distribution, 18 total. Native phases A and B passed separately.
The four explicit content/native recovery tests passed; the 256 MiB content check
measured a 2,532,483-byte Python heap peak, below its 24 MiB allowance. Those
historical results establish harness behavior, and do not replace the final
current-source gates or claim that the full combined run passed on that commit.

All generated lab configurations and database volumes used for unsuccessful or
interrupted attempts are retained locally. Only explicitly owned test processes
and containers are stopped. No real Microsoft tenant or VTW source participates.
