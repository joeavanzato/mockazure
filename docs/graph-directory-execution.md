# M3A execution record

Approved plan: docs/superpowers/plans/2026-10-16-mockazure-stateful-graph-directory.md.
Native execution started 2026-10-16 at fa3540f on feat/stateful-graph-directory.
Workspace: D:/projects/mockazure/.worktrees/graph. M1/M2 runtimes remain unchanged.

## Baseline
121 unit/integration tests passed in 455.81 seconds; one existing MSAL form_post warning.
Fresh worktree dependencies installed offline from the project cache.
Integration PR #3 remains the prerequisite for final PR targeting main.

## Task 1: directory persistence
Five new tests failed on missing schema/interfaces, then the storage and existing upgrade tests passed (6 tests).
An additional long-UPN compatibility test exposed nickname-column overflow. The fix preserves the UPN and bounds only its new derived nickname to 64 characters, including upgrade backfill.
Final targeted run: 9 tests passed in 48.41 seconds:
.venv/Scripts/python.exe -m pytest tests/integration/test_graph_directory_storage.py tests/integration/test_identity_upgrade.py tests/integration/test_identity_admin.py -q
Ruff passed on changed source/tests; whitespace check passed before commit.
The migration retains identity IDs and credentials; public object history and outbox are atomic with administrative user writes. Cross-tenant owner/member references are constrained in PostgreSQL. Competing revision writers serialize until transaction commit.

## Rulings
- New Git worktree under the user's required D: project directory preserves the M2 source checkout. Native projectless placement cannot select that destination; manual worktree lifecycle remains our responsibility.
- Continue from the verified M2 tree while PR #3 is open; the final implementation PR waits for integration into main.
- Existing admin UPNs can have local parts longer than Graph mailNickname; only the newly derived nickname is truncated. Authentication identifiers remain unchanged.
- Existing Alembic env.py needs no new model imports: the migration uses frozen DDL, not live metadata to create tables.

## Task 2: resource reads and authorization
Five route/permission tests failed on missing directory operations. Reads, restricted relationship projections and operation-specific authorization then passed alongside M2 regressions. Additional real OAuth provisioning failed on the missing User.Read.All catalog entry; after adding the directory catalog it reached the shared /me projection and exposed the old narrow projection. Both are fixed.
Final run: 11 directory-read/identity-Graph/authorization tests passed in 63.11 seconds; changed-file Ruff and whitespace checks passed.
Ruling: Keep the existing M2 generic evaluator stable and put directory-specific conjunctions, property checks and target ownership rules in Graph permissions. This avoids expanding unrelated provider permissions; future shared consumers can extract the predicate without changing the contract.

## Task 3: SQL queries and bounded relationships
Initial query checks failed on missing parser/routes. The plan's duplicate unit/integration test filename caused a pytest collection collision; the unit file is test_graph_query_parser.py so existing import behavior remains intact.
Verification: 29 combined parser/query/read tests passed in 69.94s; an additional real PostgreSQL any/escaped-startswith check passed in 7.64s. A new ne/header test failed, then all 19 parser tests passed in 1.12s after enforcing the advanced-query requirement. Ruff and whitespace checks passed.
The 5,000-user probe selected the expected ID/fields and EXPLAIN showed bounded Limit/index access. Nested-group cycles terminate and deduplicate in SQL. Hidden-membership traversal/expansion regression failed with 200 before the fix and now denies missing Member.Read.Hidden; protected filter fields cannot be inferred with a basic profile permission.
Ruling: A literal false SQL keyword does not require a bound parameter; the parser test now checks parameterization for user-supplied text/values without mistaking a safe SQL boolean constant for interpolation.

## Task 4: persisted snapshot pages
Four new paging tests failed before implementation, then passed in 33.33s. Two additional tenant-generation and delegated-user boundary checks passed in 22.41s. Changed-file Ruff and whitespace checks passed.
The 5,000-user traversal survives concurrent creates/renames/deletes, replicas, pool restart and replay. Cleanup retains the oldest live boundary and coordinates with readers.
Ruling: A deterministic HMAC child handle keyed by the predecessor makes replay produce the identical nextLink without storing raw bearer handles. The issue_cursor interface adds an optional predecessor; cost if wrong is a cursor compatibility change.
Ruling: Shared advisory transaction locks coordinate readers with exclusive cleanup without serializing readers; cost is cleanup waiting on long readers.
Ruling: Versioned relationship paging and snapshot-bound expansion finish in Task 5 alongside relationship writes; current Task 4 verification covers object collection pages. Cost is a temporary development dependency, not a released limitation.

## Task 5: shared writes and relationship snapshots
Four lifecycle/permission/atomicity tests failed on missing write routes, then passed. Two relationship tests failed on missing paging and live expansion; both now use versioned membership and public object snapshots. Combined lifecycle, relationship, query, reads, identity admin/session/authorization verification: 25 passed in 158.33s.
A further malformed reference test exposed accepting a user ID through a groups reference. Type checks now reject it. Additional bounded JSON/NUL input, deletion edge-facts and storage checks passed: 11 tests in 62.66s. Ruff and whitespace checks passed.
The common service handles Graph and administrative user writes, commit-ordered revisions, soft deletes, owner/member constraints, current permission/role checks and immutable sanitized outbox facts. Deleted users cannot be re-enabled through the existing administrative endpoint. Relationship pages and expansions now retain their original membership/property snapshot.
Ruling: Bring the bounded JSON parser forward from Task 7 because writes need the same input boundary; cost is a shared component introduced earlier.
Ruling: Split public version recording from outbox recording so one atomic deletion can update affected parent-group versions while retaining one evidence record per revision. Public property differences and relationship facts are immutable in that record, independent of history retention; cost is a richer evidence fact schema before M3B.
Ruling: Older test fixtures that bypass domain writes now seed matching relationship versions explicitly; production has no fallback from historical reads to live edges. Cost is fixture maintenance.

## Task 6: user/group delta
Three new delta tests failed on absent routes. The implementation then passed all nine delta/paging tests in 65.23s, including selected-only change tracking, immutable rounds, both route spellings and a 1,001-member change split across repeated group records.
A cleanup-boundary regression failed because a pruned delta handle returned a generic 400. Unavailable well-formed delta state now returns 410 syncStateNotFound with a configured local resync Location. Five delta tests passed in 34.24s after that fix, including hidden membership and implicit removal behavior.
Reference checked: https://learn.microsoft.com/en-us/graph/delta-query-groups and https://learn.microsoft.com/en-us/graph/delta-query-users (2026-10-16).
Ruling: Extend DeltaRequest with token-kind and configured origin so the shared reader can validate skip/delta semantics and construct local state links. Cost is an internal interface extension.
Ruling: After cursor cleanup, unavailable well-formed delta handles trigger full resynchronization; their prior select is no longer recoverable. Existing expired metadata preserves select in Location. Malformed handles and available-state binding mismatches remain errors. Cost is a broader initial resync payload after cleanup, still constrained by current permissions.
Ruling: Preserve the approved local permanent-deletion profile (reason=deleted, no restore API), although Microsoft's restorable user deletion examples use reason=changed. Retained rows are internal history only. Group delta follows Microsoft's documented omission of implicit member removals caused by object deletion. Cost is an explicit user-deletion compatibility boundary until a restorable-object profile exists.

## Task 7: batching and shared request controls
Three batch/budget/fault tests failed before implementation, then passed in 25.15s. Combined batch, boundary regression and configuration checks: 12 passed in 44.87s. Ruff and whitespace checks passed.
Read-only batches accept at most 20 independent GETs; IDs are case-insensitively unique. Each subrequest passes through the normal authenticated application boundary using an in-process ASGI transport with no socket transport or fallback. The default local profile permits 600 requests per tenant/client/resource per 60-second fixed window, separately for users/groups and shared across versions/replicas. Batch items each consume quota.
One pending next_read_429 fault per tenant/client/resource is installed only through the authenticated admin plane. It commits atomically when consumed, survives API recreation and does not intercept writes. Limits and faults are deliberately local profiles, not claims about Microsoft's changing service quotas.
Ruling: Add a dedicated admin graph router for fault controls instead of expanding the existing identity router. Cost is one small additional module.
Ruling: Use HTTPX ASGITransport for internal dispatch to retain the complete request/auth/error/quota path without network I/O. Bound response bodies to 8 MiB per item/32 MiB per batch; cost is explicit rejection of unusually large batches, which callers can split.

## Task 8: safe directory datasets and CLI
Three dataset tests failed on missing APIs/interfaces. Public snapshot round-trip into a separate fresh PostgreSQL database, inert imported users, atomic validation, delta visibility, streaming and existing tenant export/job behavior then passed: 10 tests in 57.98s.
The combined 10,000-object cap and explicit external-owner export boundary passed in 7.76s. A new early-disconnect test exposed a checked-out snapshot connection when response headers failed before the generator started. SnapshotResponse now owns cleanup across the entire response call; all five dataset tests passed in 39.40s. Directory CLI help exposes import/export/cleanup. Ruff and whitespace checks passed.
A scope audit found user PATCH accepting UPN/mobilePhone/businessPhones beyond the approved profile; a test failed before removal, then all six mutation tests passed in 36.75s. A second regression exposed anonymous Group.Create groups becoming modifiable after broader consent; their internal restriction is now persistent and all seven mutation tests passed in 39.68s.
Ruling: Version-one datasets form a closed user/group graph, with no credential or service-principal restoration. An export with SP owners fails explicitly rather than dropping references; M3C will extend the format. Cost is that such tenants cannot use the A public dataset exporter yet.
Ruling: Preserve dataset provenance in the import outbox record (source/description/seed/generator scalar fields) and export the latest import provenance at the snapshot. This avoids adding a second metadata store; cost is that mixed imports report the latest dataset provenance rather than per-object lineage.
Ruling: Round-trip ID preservation is verified in a fresh database because identity object IDs remain globally unique within an instance. Cost is no same-instance clone with unchanged IDs; collisions fail atomically.
Public datasets are fixtures, not identity/authorization backups: credentials, sessions, grants, roles and private operational flags are not restored. Administrative import is create-only, limited to 10 MiB/10,000 combined users and groups; relationships must reference included objects. Graph writes remain independent of this fixture format.

## Task 9: real-client qualification (in progress)
Acceptance failed on missing tools.verify_graph after isolated M3 readiness and
5,000-user provisioning. Users/Groups 2.35.1 archives and complete trees are pinned;
six preparation-integrity tests passed. Actual Python SDK paging reached user creation
and exposed its @odata.type annotation. A regression failed with HTTP 400, then passed
after validating matching annotations and adding generated delta() aliases.
Capability metadata regression failed at milestone 2, then three wire/capability tests
passed in 8.76s. One intervening test attempt hit PostgreSQL recovery, which completed;
the subsequent run passed without a database reset.
Ruling: Support validated generated SDK type annotations and function-call delta aliases.
Cost is a few equivalent accepted wire forms, with no new resource or mutation scope.
Ruling: Use the genuine PowerShell generated delta cmdlet for bootstrap, then its
Invoke-MgGraphRequest for opaque links because this pinned cmdlet exposes no delta-link
parameter. Cost is a documented client workflow detail rather than generated-only traversal.

Both real SDK workflows passed application/delegated user/group lifecycle, 5,000-user
paging, membership delta, deletion and permission denial. The Python Kiota parser
materializes UUID-looking untyped delta values as UUID objects; the harness compares
their canonical string IDs, preserving exact identity checks.
Native qualification passed snapshot/replay across replicas, API and PostgreSQL restart,
delta resume, foreign reference rejection, wrong audience, shared one-shot 429 and
clean-wheel CLI export. The final run also injects 429 before the generated Python
SDK read to verify its default retry middleware without a harness retry wrapper.
The first complete suite had 186 passes and one stale M1/M2 capability/users-501
assertion in 403.95s. That test now preserves old operations as a subset, expects
unauthenticated users to return 401, and keeps the unknown-provider-route 501 check.
Full-suite rerun is in progress.

Final isolated qualification passed: 14 runtime checks, three client profiles, both application/delegated modes for each. Qualified at 2026-10-16T20:15:12.719853+00:00.
Source fingerprint: 595c88500f500abc39179b93ae226d9fe52919dd50f0390d1166d8ee9b8a30c6
Wheel SHA-256: 34880eb5c6d8e146c0469acfbc999450cf9731ecec4fbb9d241dac35649c89a8
Manifest SHA-256: 46589d4b1bc89c7456a710cbc45fe664bea0c55b68e4869a570e5d5dbb33cc7c
Runtime image: sha256:cb23e7576f481a8b62140f8101b1f810857a014e087e781ec228d0a14d4b3601
Ruff, package provenance/distribution boundaries and whitespace checks passed. Final regression rerun and independent review are pending.

## Independent final review
One fresh gpt-6-astra reviewer inspected fa3540f..8d8d533 in read-only passes.
No Critical or Minor findings. Four Important findings enter one TDD fix pass:
deleted hidden-group historical member disclosure; imported Guest write boundary;
malformed NUL/oversized numeric filters; nullable in-list result semantics.
All four grades are retained based on their effect on callers.

Final: Ruling: Service-principal owner fields remain live in A because no supported A
route mutates those projected fields. M3C must version them before adding writes.
Cost if wrong: future SP changes could violate relationship snapshot replay.
Final: Ruling: Audit projection, collector evidence, conditional access and broader
security administration remain explicitly M3B/C. Cost: A cannot validate those workflows.
Final: Ruling: Issued access-token permission claims retain the parent design's semantics;
grant changes do not rewrite existing token claims. Current subject/role checks remain.
Cost: grant revocation is not immediate token-permission revocation.
Final: Ruling: Functional 5,000-user qualification does not establish 100,000-user or
million-evidence-record throughput. Cost: scale targets need the later measured gate.
Final: Ruling: Fidelity claims stay within the documented contract and public references;
real-tenant testing remains separately scoped. Cost: unmodeled service differences can remain.
Final: minor (deferred): none.

The pre-review complete regression suite passed 187 tests in 492.24s.
Final: fixed deleted hidden-group disclosure — expansion and delta reproductions
failed with 200 before the fix, then passed with 403 while authorized historical
reads remained intact. Combined hidden traversal checks: 3 passed in 8.87s.
Current and snapshot recursive traversals use independent anonymous CTE names;
the existing nested-hidden test caught their initial name collision before correction.
Final: fixed imported Guest provider writes — PATCH returned 204 before the fix;
atomic rejection/admin separation regression passed in 3.47s.
Final: fixed malformed filter errors — NUL produced 503 and oversized integers 500;
all six collection/count/batch cases now return Graph 400 (25.05s).
The count probes were corrected to supply only the supported filter option, so
they reproduce the literal failure rather than an unrelated option rejection.
Final: fixed nullable in-list semantics — null-only comparison failed with an empty
result, then null-only/mixed/non-null PostgreSQL queries passed in 5.43s.
All four findings are addressed in one fix pass; full expanded suite and regenerated
runtime qualification are now running. No second review is requested.

Final: Ruling: Publish the completed M3A change as a draft PR targeting main while M2 integration PR #3 remains open. This preserves the approved target and makes the work reviewable without merging for the user. Cost: the draft comparison includes prerequisite M2 changes until #3 merges; mark ready after that integration.


## Completed verification after review fixes

All four Important findings were reproduced RED and fixed GREEN in one pass.
The complete expanded unit/integration suite passed 197/197 in 514.30s:
.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q
The one warning is MSAL's existing recommendation to use form_post.

The final acceptance gate passed 1/1 in 6.45s:
.venv/Scripts/python.exe -m pytest tests/acceptance/test_graph_distribution.py -q

After rebuilding the wheel and isolated M3 image, the verifier passed all 14 checks
and all three real SDK profiles in both application and delegated modes in 172.57s:
.venv/Scripts/python.exe tools/verify_graph.py --report work/graph-directory-report.json

This post-review report supersedes the pre-review qualification above.
Qualified at: 2026-10-16T20:29:55.206746+00:00
Source commit: fb81c88f391437a5000dfe35b006bab4ee9b2111
Source fingerprint: bbcf96e200899fae5ff09ed8b639a36e2e75242d7ffa18da2802b1bb027f6adf
Wheel SHA-256: 31c109b1ff2b2cc17eeead71b17a756e7878bbf0ba2f8e46d55923c72731c88a
Manifest SHA-256: 46589d4b1bc89c7456a710cbc45fe664bea0c55b68e4869a570e5d5dbb33cc7c
Lock SHA-256: ccbec428f8204ed3ef3a7536dcbc080c25611f27a14433f8f0a0f39265c90cfb
Runtime image: sha256:7e3843ef503be14efd2337f72606bfbb59bc761a986ddfabdcad98ba7df6a21a

Ruff, package provenance/distribution boundaries and git diff --check passed.
Only documentation changed after these runtime checks; the qualified source
fingerprint remains unchanged. Network isolation checks passed for native Python,
container Python and container PowerShell; no real tenant was used.

Final: fixed deleted hidden-group historical disclosure — expansion/delta
reproductions RED to GREEN, full suite 197/197.
Final: fixed imported Guest provider writes — atomic rejection/admin separation
reproduction RED to GREEN, full suite 197/197.
Final: fixed malformed filter error shaping — six collection/count/batch
reproductions RED to GREEN, full suite 197/197.
Final: fixed nullable in-list semantics — PostgreSQL null/mixed/non-null comparison
reproduction RED to GREEN, full suite 197/197.
Final: minor (deferred): none.

Tasks 1–9 implementation, verification and independent review are complete.
Publication follows the recorded draft-PR adjustment while prerequisite PR #3 is open.
Audit/evidence projection and collector gates remain M3B; broader security
administration remains M3C. Large-scale throughput is not established by the
functional 5,000-user qualification.

All execution decisions and their costs are preserved in
[the complete rulings record](graph-directory-rulings.md).
