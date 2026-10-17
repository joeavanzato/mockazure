# M3C native execution checkpoint

Active worktree: D:/projects/mockazure/.worktrees/security
Branch: feat/security-administration
Base main: 5cdcf5a0b1228f44cc8561e2e4217e0058355b26

The user authorized uninterrupted M3C, M4, M5 and M6 delivery with reasonable
decisions recorded, no routine design/plan approval pauses. PRs remain for user
merge. No real tenants, VTW access, host trust/DNS/firewall changes or registry
publication. Preserve existing worktrees and runtimes.

## Completed tasks

1. Official structural schemas and permission IDs: 356542f.
   Metadata v1.0/beta pin 34b8e9c29032c82352a6fd7138be0a864a842bb2.
   REST permission reference and two supplementary existing group permissions
   have independent revision/hash/license provenance in security/data.
2. Canonical application/SP/device reads and persistent security snapshots:
   3169e34. Upgrade preserves OAuth clients and stable distinct application
   object IDs. Tenant/caller/query/expiry-bound paging stores rows in PostgreSQL;
   one INSERT SELECT captures a coherent collection without an all-rows Python list.

## Active task 3

Implemented and tested in the current checkpoint:
- Application/SP/device mutation routes; tenant-local objects and owners.
- App-only ownership checks and delegated device permission restrictions.
- Secret and public-certificate credential changes affect real local OAuth.
  Future-start credentials fail; revocation blocks new tokens while existing
  access-token behavior remains unchanged.
- Security audit facts and projection share the evidence outbox transaction.
  Private credential data is excluded from public audit differences.
- Owner expansion survives intervening ownership edits and limits user disclosure.
- Canonical Graph configured permissions are exposed as real permission GUIDs.
- Control-plane identity writes share the security lock.

Task 3 is **not complete**. Before recording task-done:
- Finish operation parity for alternate-key mutations and credential/owner routes;
  reads already support application/SP appId lookup.
- Exercise and finish device alternate-key/uniqueness and operation-specific role
  constraints, including owner types and non-Windows update restrictions.
- Check credential key disclosure (default versus explicit select), certificate
  replacement/error paths, deletion versus administrative re-enable, and
  cross-tenant owner references using existing foreign objects.
- Verify security audit export/reimport and a published Graph audit read, beyond
  source projection validation; do not waive existing gates.
- Keep unsupported write fields explicit; do not store a policy switch as if it
  were enforced when no evaluator consumes it.
- Review source-visible operational state for bootstrap/migration paths.
- Run task checks, commit, then task-done against task base 3169e347d6b5330fda6dbae180476c24f9a34d6a.

No final milestone review or qualification has happened yet. Tasks 4–10 remain:
effective grants, roles, method inventory, policy persistence/enforcement,
real-client qualification, full regression/review/PR.

## Evidence and continuation

- Baseline: 144 checks passed.
- Reference/unit regression: 162 checks passed.
- Task 2 regression: 208 checks passed in 62.30s.
- Task 3 core regression: 210 checks passed in 50.51s.
- Additional audit null-field/secret rejection checks: 2 passed.
- Checkpoint export/security validation: 12 passed in 17.99s.
  Output: work/security-checkpoint-validation.log.
- Full temporary ledger and task briefs:
  .superpowers/sdd/2026-10-16-mockazure-security/
- Python: .venv/Scripts/python.exe; Ruff: .venv/Scripts/ruff.exe.
- Test DB project: mockazure-security-tests, loopback port55438.
  Set MOCKAZURE_TEST_DB_PORT=55438 for integration tests.
  Compose: work/compose.security-tests.yaml. Own secret: .runtime/test-db.password.
- Do not run concurrent integration suites against the same test runtime.
- Writes under D: use the authorized sandbox escalation; never move work into VTW.
- Native execution: no implementation subagents; one independent final reviewer
  at each completed milestone.
- Hourly heartbeat continue-mockazure-m3c-through-m5 is named
  Continue MockAzure through M6 and remains active.
- M4, M5 and M6 follow in order. The release scale target is 100,000 users and
  1,000,000 evidence records; report actual measurements and unmet gates honestly.
