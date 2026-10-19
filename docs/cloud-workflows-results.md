# Cloud workflow qualification

Phases A-F have individual implementation and native-client evidence. Phase G is
qualifying the combined source; this document does not claim that the final
matrix, full-scale run or packaged distributions have passed yet.

## Required combined evidence

| Gate | Command / evidence | Requirement |
| --- | --- | --- |
| Native clients | `python -m tools.verify_cloud_workflows --phase all --report work/cloud/all.json` | All six phases, four declared ecosystem cells, pinned versions, one commit and fingerprint; explicit not-applicable and REST-fallback cells remain visible. |
| Cloud scale | `python -m tools.benchmark_cloud_workflows --records 100000 --binary-bytes 268435456 --readers 8 --report work/cloud/scale.json` | Exact IDs, 256 MiB hash, eight readers, two API replicas/restarts, 32 acknowledged concurrent writes, tenant isolation, queue contention, file ranges and diagnostics. |
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
