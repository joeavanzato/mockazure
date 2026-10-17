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

5. Directory-role management:
   - Canonical user/service-principal roles govern existing Graph/Exchange checks.
   - Official template IDs, tenant-scoped assignments, permission/role checks,
     bounded principal/definition expansion and stable snapshots.
   - Legacy activated roles persist after their last assignment is removed;
     member disclosure follows the caller's directory-object permissions.
   - Role mutation audits publish and export under Graph v1.0.
   - Affected unit/integration regression: 255 passed in 169.34s.
     Log: work/task5-regression.log. Ruff and diff checks pass.

6. Authentication-method inventory:
   - Canonical password descriptors and persisted phone/email/Authenticator/FIDO2
     enrollments; concrete collection/item paths and me/id/UPN addressing.
   - Official generic/granular permission IDs, delegated role checks, self scope,
     phone masking and no credential or fabricated challenge disclosure.
   - Authenticated descriptor configuration and indexed bounded user lookups.
   - Fresh app persistence, invalid/secret inputs, deleted/foreign-user isolation.
   - Affected unit/integration regression: 267 passed in 207.21s.
     Log: work/task6-regression.log. Ruff and diff checks pass.

7. Conditional Access persistence:
   - v1.0 policy/IP named-location CRUD and typed supported-condition validation.
   - P1/P2 entitlement, documented permission conjunctions and delegated roles.
   - Tenant references, nested PATCH, named-location deletion protection.
   - Stable snapshots, query bounds, fresh app persistence and policy audit export.
   - Affected unit/integration regression: 287 passed in 130.79s.
     Log: work/task7-regression.log. Ruff and diff checks pass.

## Next work

Task 8: enforce Conditional Access across delegated OAuth and publish immutable
decision evidence with permission-based disclosure. Tasks 9–10 qualify real clients
and perform final review/PR. Task 7 base was
7a2efc22892fec45b79ca1cd83d6d5e37a565f9e; run task-done if the ledger does not
record completion before starting Task 8. M3C is not qualified yet.
See docs/security-contracts.md for implemented bounds.

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
