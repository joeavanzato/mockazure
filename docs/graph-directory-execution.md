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
