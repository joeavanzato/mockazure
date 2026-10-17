# Foundation execution and review

Approved implementation: native execution of the six-task foundation plan.
Implementation checkout: `D:\projects\mockazure\.worktrees\foundation`.
Branch: `feat/foundation`. VTW was read only for the four allowlisted Git blobs.

## Delivered increments

1. Standalone package, Apache-2.0, exact tracked-source provenance.
2. Project-local settings, concurrent-safe initialization, credentials and TLS.
3. PostgreSQL migrations and persistent tenant metadata.
4. Authenticated management API and native HTTPS CLI.
5. Durable export snapshots, retry/lease fencing, import and real worker recovery.
6. Pinned Docker packaging, native parity, isolated networking and live qualification.

Microsoft provider operations remain explicitly unsupported in this milestone.

## Independent review and response

A fresh read-only reviewer inspected the complete implementation range
`2402bd1..40581ba` against the approved plan. No critical findings.
One important finding and two minor findings were reproduced and fixed:

| Finding | Resolution | Regression evidence |
| --- | --- | --- |
| Malformed Host accepted; slash redirects reflected request authority | Validate one complete DNS/IPv4 authority and optional valid port; disable automatic slash redirects | Malformed/duplicate hosts rejected; alternate-port slash request has no redirect |
| PostgreSQL-incompatible NUL input reached persistence | Reject NUL in display names and licence labels | Both create and import return 422 without inserting a tenant |
| Unknown Alembic revision escaped sanitized error handling | Catch Alembic CommandError alongside database exceptions | Direct migration fails safely; CLI exits without traceback or DSN |

The five regression cases failed before the changes and passed afterward.
The approved native workflow uses one independent review and one response pass;
it does not require a second reviewer. No reported finding was left unresolved.

## Execution decisions and costs if assumptions change

| Decision | Reason | Cost if wrong |
| --- | --- | --- |
| Project-local manual worktree | Native worktree placement could not honor the required D: directory | Manual cleanup; retain it while the PR and local deployment use it |
| Pinned HAProxy TCP ingress | Internal Docker bridges on this host did not publish usable native-client ports | One extra service and TCP hop; no protocol rewriting |
| Read-only runtime TLS validation | Shared container credentials are mounted read-only | Invalid material requires explicit repair/renewal rather than automatic replacement |
| This instance uses HTTPS port 28443 | 8443 and 18443 were already occupied; existing services were preserved | Clients must use the generated endpoint; fresh-install default remains 8443 |
| Native Python 3.12.13 and container Python 3.12.15 | Both satisfy the pinned 3.12 baseline and passed their checks | Patch-specific issues require reproduction in both environments |

## Explicit rulings on review boundaries

Every behavior the reviewer set aside was considered; none was silently dropped.

| Considered behavior | Ruling | Consequence if the assumption changes |
| --- | --- | --- |
| Provider APIs, OAuth, permissions, collectors, OData/delta, reset, retention, scale and automated backup | Accept the explicit later-milestone boundary | Foundation cannot satisfy provider collection workflows yet |
| Legacy reference and dependency lock detailed review | Accept archival/reference exclusion; inspect distribution boundaries | Upstream/legacy behavior is not certified by this review |
| Worktree, ingress, port, Python patches and read-only TLS | Accept the documented execution decisions above | Their operational costs remain as listed above |
| Interrupted initialization and stale locks | Accept documented fail-closed recovery | Operator must inspect/restore rather than retry blindly |
| Leaf/key replacement is not atomic as a pair | Accept fail-closed validation and explicit renewal repair | An interrupted renewal can require downtime until repaired |
| Windows inherited ACLs, container root and runtime CA access | Accept the documented local deployment trust model | This is not a hardened multi-user hosting boundary |
| Expired owner completes before reclamation | Accept owner/attempt fencing as the specified contract | Lease expiry alone does not revoke an unreclaimed claim |
| No worker heartbeat | Accept bounded metadata publication | Longer future jobs need lease extension or different execution rules |
| Manually corrupted job payload | Outside supported API-produced data contract | Arbitrary database corruption may need operator repair |
| Worker clocks and claim/reaper contention | No demonstrated correctness defect in this local milestone | Distributed clocks and high-throughput behavior need later qualification |
| UUID paging during concurrent insertions | Accept documented keyset pagination | It is not a historical collection snapshot |
| Import replacement supplies complete metadata | Accept the explicit TenantCreate replacement contract | Callers must preserve any metadata they wish to retain |
| Readiness checks rather than implicit startup migrations | Accept explicit migration ownership | Operators must migrate before using service data routes |
| Generic CLI messages, early response request-ID headers, and Allow headers | No material M1 contract failure; unchanged | Diagnostics and protocol polish may be improved later |
| Qualification freshness and external-request count | Accept the fresh observed run, guards and network checks | Reports are run-specific evidence, not universal or permanent containment claims |
| PostgreSQL disaster recovery and backup restoration | Beyond container-recreation qualification | Full restore procedures remain an operational responsibility |
| Reviewer did not rerun tests | Accept read-only review backed by executor evidence | Review findings are source inspection, while test results come from execution |

## Verification evidence

The live report is generated under ignored `work/foundation-report.json`.
It records real CA-verified HTTPS, two API processes, two workers, preserved
tenant/export data after database-container recreation, unchanged CA/admin
identity, blocked external connection attempts, Docker-only initialization,
and a clean offline wheel installation.

Tests additionally kill a claimed worker and verify recovery without duplicate
logical exports. PostgreSQL integration uses a dedicated test instance and
generated database names. All application/test data remains under the project.

The final completion message and PR record the final suite count. Runtime reports,
private credentials, certificates, database files, caches, and scratch output
are deliberately excluded from Git.

Final verification: **50 tests passed**, Ruff clean, distribution/provenance checks passed, and live qualification passed after the review fixes. No reported review findings were deferred.
