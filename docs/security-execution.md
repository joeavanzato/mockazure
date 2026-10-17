# M3C native execution checkpoint

Active worktree: D:/projects/mockazure/.worktrees/security
Branch: feat/security-administration
Base main: 5cdcf5a0b1228f44cc8561e2e4217e0058355b26

The user authorized uninterrupted M3C, M4, M5 and M6 delivery with reasonable
decisions recorded, no routine approval pauses. Native implementation; one
independent final reviewer per milestone. PRs remain for user merge. No real
tenants, VTW access, host trust/DNS/firewall changes or registry publication.
Preserve existing worktrees and runtimes.

## Completed implementation

1. Official structural schemas and permission IDs: 356542f.
2. Canonical application/SP/device reads and persistent snapshots: 3169e34.
3. Canonical object/owner/credential mutations and audit:
   - Application/SP object ID and appId routes have matching writes/actions/owners.
   - Device id/deviceId routes share tenant-unique alternate keys and user-only owners.
   - Device creation/owner writes require delegated access; operation-specific
     roles restrict Cloud Device Administrator and Windows 365 Administrator.
   - Secret/certificate writes affect actual local OAuth; failed replacement rolls
     back; future/revoked credentials cannot authenticate. Deleted applications
     cannot be reenabled by the ordinary administrative enable endpoint.
   - Default reads mask certificate key bytes; explicit select returns public DER.
   - Owner expansion preserves snapshots and limits user disclosure.
   - Control-plane bootstrap redirects and configured permission GUIDs are visible
     through Graph and survive subsequent mutations. New admin credential metadata
     has its actual creation time; legacy unknown start times remain the migration
     fallback and do not change previous authentication validity.
   - Security audit facts/outbox are atomic, validate public v1.0 differences before
     enqueue, publish to Graph, and export/reimport without private credential data.
     Native security exports retain v1.0 rather than being mislabeled beta.
   - Resource SP catalogs refresh from the pinned permission catalog.
   - Unsupported write switches (including appRoleAssignmentRequired) are rejected.
     Non-Windows app-only extensionAttributes are currently outside this declared
     write profile; they return an explicit error, not a successful ineffective write.
   - Bootstrap is configuration, not invented Graph activity; admin identity writes
     share the security lock and normalized rows.

4. Effective delegated grants and application permission assignments:
   - Graph CRUD updates the canonical grants consumed by OAuth; native consent
     uses the same security lock.
   - Principal/AllPrincipals scopes, per-permission assignment IDs, resource-side
     appRoleAssignedTo and appId addressing, stable SQL snapshots.
   - Permission conjunctions and delegated privileged-role checks.
   - Graph and Exchange.ManageAsApp configured/effective permission parity.
   - Actual per-permission creation metadata; unknown administrative assignment
     times are null. Metadata is not an authorization authority.
   - Grant mutation audits validate, publish and export/reimport.
   - Focused checks: 9 passed. Full unit/integration suite: 414 passed, 1 existing
     MSAL response-mode recommendation warning in 833.67s. Log:
     work/task4-regression.log. Diagnostic stack dumps were slow CLI tests,
     not failures. No DB test run remains active.

## Next work

Task 5: directory-role definitions/assignments and legacy directoryRoles member
views. Official role template IDs are saved to work/references/role-templates.json.
Task 4 base was 8189d26c98a68448ddac4b6ff029a8b6e3ee1f13; run task-done if the ledger
does not yet record its completion, then task-start5. Tasks 6–10 cover methods,
policy persistence/enforcement, real-client qualification and final review/PR.
M3C is not qualified yet. See docs/security-contracts.md for implemented bounds.

## Verification and continuation

- Task 3 affected regression: 230 passed in 111.25s.
  work/task3-final.log. Ruff and git diff checks pass.
- RED/GREEN evidence includes six operation parity gaps, three bootstrap metadata
  gaps, invalid source differences and a real security audit export/reimport failure.
- Full ledger/task briefs: .superpowers/sdd/2026-10-16-mockazure-security/
- Python: .venv/Scripts/python.exe; Ruff: .venv/Scripts/ruff.exe.
- Test DB: mockazure-security-tests, loopback55438.
  Set MOCKAZURE_TEST_DB_PORT=55438. Compose: work/compose.security-tests.yaml.
  Own ignored secret: .runtime/test-db.password. Do not run concurrent DB suites.
- Writes under D: use authorized sandbox escalation.
- Hourly heartbeat continue-mockazure-m3c-through-m5 is named
  Continue MockAzure through M6 and remains active.
- Continue M4, M5, M6 in order. M6 includes actual measurements at
  100,000 users/1,000,000 evidence records; no unverified scale claims.

## Additional primary operation references

- https://learn.microsoft.com/en-us/graph/api/device-post-registeredowners?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/device-update?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/device-delete?view=graph-rest-1.0
