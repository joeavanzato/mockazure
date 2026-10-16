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
