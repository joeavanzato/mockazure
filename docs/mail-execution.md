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
Next: Task4 Exchange commands and original compiled EXO transport.
Full M4 runtime remains unprovisioned; allocate separate ports/subnets before setup.
