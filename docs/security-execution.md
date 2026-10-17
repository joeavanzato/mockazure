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

8. Conditional Access enforcement and immutable decision evidence:
   - Current policy evaluation at login, consent, MFA, code/device redemption and refresh.
   - Transitive group/exclusion matching, actual peer IPv4/IPv6, persisted client category.
   - Block precedence, enforced MFA, report-only/disabled behavior and licence fail-closed.
   - Attempt-time decision evidence, current reader permission/role omission, enum preference.
   - Fresh-instance evidence import/reexport and pending-flow policy changes verified.
   - Full regression:455 passed and one old all-null client category assertion failed;
     the assertion now verifies exact known categories. Focused rerun:15 passed in29.43s; work/task8-final.log. Ruff and diff checks pass.

## Current status and next work

M3C is qualified after final review fixes:522 unit/integration tests,20 runtime
gates,three real SDK profiles in both modes and20 original collector cases.
The source-bound final qualification took181.5455s. See security-verification.json.
Publish the M3C PR, then continue directly into M4,M5,M6; user merges.
Earlier checkpoint sections below are historical evidence.

## Verification and continuation

- Task 3 affected regression: 230 passed in 111.25s.
  work/task3-final.log. Ruff and git diff checks pass.
- RED/GREEN evidence includes six operation parity gaps, three bootstrap metadata
  gaps, invalid source differences and a real security audit export/reimport failure.
- Final ledger: docs/security-native-ledger.md; temporary plan workspace is removed after publication.
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

## Task 9 API closure checkpoint

Original collector source required registration reports and user signInActivity.
Both now use persisted tenant state and real authentication evidence; sign-in
summaries survive evidence retention and paged selections remain stable.
The registration descriptor profile explicitly disables physical MFA/passwordless/
SSPR capability services, returning false for their non-nullable booleans rather
than claiming enrolled descriptors can perform those challenges.

Generated SDK root @odata.type annotations are accepted only when they match
the resource type. Capitalized known user selections used by MES normalize to
canonical fields without restricting unrelated licensing selection contracts.

Verification: 365 affected unit/integration tests passed in 270.07s
(work/task9-closure-regression2.log); seven SDK serialization cases and the
licensing regression passed in the focused run. Ruff and diff checks pass.
Qualification scripts and runtime gates remain unfinished; these results do not
qualify M3C. Its own runtime uses HTTPS 32443, PostgreSQL 55440, planned native
API32444/32445 and managed identity32446. No host networking settings changed.

## Task 9 runtime qualification

Verified API closure commits57f524f and25abb8d are pushed. Final source-bound
qualification passed all20 runtime gates, three native/container SDK profiles
with15 checks per application/delegated mode, and all20 original collector cases.
Qualification took236.926497s. Original output records verified:
Hawk credentials 10; MES policies 2, grants 184, methods 4, role assignments 6.
All cases include both successful and denied access; no new output waiver.

The complete unit/integration regression passed 474 tests in 534.66s.
The MSAL form_post recommendation is the sole warning. The final new gate
and distribution acceptance run passed 6 tests in 0.43s. Ruff/diff checks pass.
Final review is pending. Source fingerprints and hashes remain authoritative;
work/security-report.json and docs/security-verification.json record the evidence.

## Final review fix pass

The independent whole-branch review of 5cdcf5a..39277b5 identified five important
issues: UUID selector matching, managed client-mode/credential mutation, managed
tombstone resurrection/current authorization, orphan owner navigation/mutations,
and a denied-JSON collector oracle gap. All entered one consolidated fix pass.
No second review was dispatched; no findings were declined for judgment.

Observed RED: 31 API regressions, two selector unit cases, four explicit managed
password actions, and four JSON denial cases reproduced the defects. The initial
owner preservation test needed the documented Directory.Read.All permission;
after correcting that fixture, it reproduced a live resource-owner omission
introduced by the fix and was corrected as part of the same pass.

Focused GREEN: 33 policy tests; 30 managed/lifecycle/owner tests; nine collector
oracle tests. Final full regression and rebuilt runtime qualification remain
pending. The pre-fix Task10 run passed476 tests in869.32s; its source-bound runtime
qualification passed20 gates/three SDK profiles/20collector cases in405.989998s.
These historical runs are not evidence for the final fix bytes.

Deferred minor: retain upstream LICENSE.docs line52 whitespace verbatim.

## M3C qualified after final review

Final fix commit: 26722043c4bf342768b21d130dfc90f17dadefa6.
All 522 unit/integration tests passed in 580.74s, with the existing MSAL
form_post recommendation as the only warning. Source-bound qualification passed
all20 runtime gates, all three SDK profiles (15checks in each application/delegated
mode), and all20 original collector cases in 181.5455s.

Five important reviewer findings were reproduced and fixed in one pass.
The live-resource-owner preservation regression also passes. Ruff and working
diff checks pass. The upstream licence-notice whitespace is a documented minor.
No new output waiver, no real tenant/VTW access, no host networking changes.

Next: push the final documentation, create/attach the M3C PR against main, preserve
this runtime/worktree, and start M4 from this verified feature branch. The M4
design/plan drafts in work/ were prepared from original collector source and
official Graph/Exchange references while validation ran.
