# M4 Native execution checkpoint

Worktree: D:/projects/mockazure/.worktrees/mail
Branch: feat/exchange-audit
Base:351b182 (qualified M3C; PR7).
Spec: docs/superpowers/specs/2026-10-17-mockazure-mail-design.md
Plan: docs/superpowers/plans/2026-10-17-mockazure-mail.md

Continue Native implementation through M4,M5,M6 without routine approvals.
All reasonable choices are delegated; record rulings. No implementation agents,
one fresh independent final reviewer per milestone. User merges PRs.

Never access VTW or real tenants, copy unrelated secrets/untracked files, change
host trust/DNS/firewall, or modify predecessor worktrees/runtimes. Use own M4
runtime, test database and credentials. Public pinned references/artifacts may
be reused with hash verification.

M3C final evidence:522 unit/integration tests580.74s;20 runtime gates,three
SDK profiles and20 original collector cases181.5455s. All five final reviewer
findings were reproduced and fixed. PR:https://github.com/joeavanzato/mockazure/pull/7

M4 environment:uv sync --frozen --all-groups --offline; Python3.12.13.
Fresh unit baseline:210 passed10.43s. Task1 implemented pinned mailbox/UAL schema
roots, contextual aliases, bounded audit dictionaries and canonical permissions.
Validation:10 focused contracts0.46s;220 full unit tests8.15s.
Task2 implemented PostgreSQL mailbox/settings/folder state, explicit command scopes,
current authorization checks, forwarding and immutable transactional mail facts.
227 unit/mailbox tests15.97s;24 affected contracts/migration/directory tests16.73s.
Graph UPN update now supports verified-domain rename; historical facts remain immutable.
Own test DB:compose.test.m4.yaml, loopback55441; set MOCKAZURE_TEST_DB_PORT=55441.
Networks10.231.92/24 and10.231.93/24; fresh own .runtime/test-db.password.
Task3 implemented Graph inbox rule CRUD, hidden/read-only rules, serialized ordering
and immutable PostgreSQL page versions with principal-bound continuations.
233 unit/mail/rule/migration tests25.82s;5 focused rule tests8.29s.
Task4 implements fixed Exchange configuration/rule proxies plus real compiled
OData mailbox/statistics/folder/CAS routes. Six original Exchange module checks
pass, including exact primary/archive counts/bytes and the command mutation cycle.
239 unit/mail/Exchange regressions passed29.94s.
Own M4 runtime:compose.m4.yaml / compose.clients.m4.yaml, HTTPS34443, PG55442,
networks10.231.94/24 and10.231.95/24, fresh .runtime/m4 secrets/CA.
Public17 archives/extracted trees hash-verified; no original module modification.
Task5 implements immutable trace/detail import, indexed filters, scope-aware reads,
5002-row continuation and rolling100/300second throttling. Four focused tests18.43s;
232 affected unit/Exchange/migration regressions33.83s. Runtime migration027 and
original trace client/collector qualification remain for the runtime gate.
Task6 adds native UAL projection, bounded historical import/export, credential-field rejection and replay/restart recovery. Broad regression:302 passed168.97s; final bounds/privacy/schema/projection checks:21 passed7.07s. Next: Task7 durable Graph audit query jobs and retention-safe result paging.

Task7 implements durable SQL UAL jobs/results, workload/Purview authorization, retained snapshots, lease fencing and opaque paging. Nine acceptance tests31.59s;255 affected regressions57.94s; original MES empty-filter fix4tests4.36s. Next: Task8 real clients and runtime parity, then original collectors and final review.
`nTask8 harness implemented and diagnostic real-client checks pass in both modes:Graph Python native/container and Graph PowerShell8checks each;Exchange7checks. Full clean-wheel/restart/provenance report remains pending before Task8 completion.
