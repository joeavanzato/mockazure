# M3B execution decisions

This records the execution ledger's decisions in order. The user accepted the narrow original MES audit JSON limitation;
final qualification passed with that limitation explicitly retained.

1. Use a Git worktree under the user-owned D: repository because the desktop
   worktree tool targets the projectless C: workspace. Cost: no native worktree attachment.
2. Create a separate test instance and credential on port 55437 after automatic
   review rejected copying an existing test password. Cost: another isolated instance.
3. Treat pinned Microsoft metadata and version-specific REST documentation as
   authoritative over planning field lists. Cost: supported subsets must be documented separately.
4. Allocate checked-unused test subnets 10.231.76.0/24 and 10.231.77.0/24 because
   Docker's default pool was exhausted. Cost: two additional local networks.
5. Correct modifiedProperty.displayName and v1.0 service-principal fields; use
   empty strings for unknown required string identifiers. Cost: the empty identity
   representation is a disclosed local choice, not inferred authentication.
6. Include generation in ProjectionBatch to fence resets. Cost: an internal
   interface extension propagated through producers and consumers.
7. Run focused required regressions per task and the full suite at the end.
   Cost: unrelated regressions may be discovered later; the full gate remains mandatory.
8. Enable Docker init for the new PostgreSQL test container after orphaned
   health-check children caused repeated server recovery. Cost: an additional init process.
9. Use short pytest tracebacks and rotate the disposable test credential after an
   earlier verbose failure exposed its connection arguments. Cost: less default
   traceback context; preserved runtime credentials were unchanged.
10. Move only the disposable test database to 2 GiB tmpfs after Windows bind
    storage produced 4.9–5.5 second connections. Cost: the test cluster itself is
    ephemeral; persistent-storage restart checks run separately on M3B.
11. Have task-completion helpers validate finished final logs rather than repeat
    unchanged tests. Cost: evidence must remain tied to the tested source state.
12. Mark unknown legacy operations legacy_incomplete without inventing provider
    activity. Cost: these historical records cannot supply complete Graph evidence.
13. Allow audit oldValue/newValue strings up to 4 MiB while ordinary strings remain
    limited to 16 KiB. Cost: larger bounded payloads; these are local capacity limits.
14. Preserve consent grants already committed before a later MFA denial.
    Cost: denial does not reverse a prior explicit consent decision.
15. Leave unknown clientAppUsed and exact MFA-step time null.
    Cost: sparse evidence instead of invented client or timing claims.
16. Recheck enabled applications, service principals and delegated users in shared
    resource authorization. Cost: disabled principals receive 403 even before token expiry;
    existing grant-claim and token-lifetime semantics remain intact.
17. Permit catalog-validated managed-identity application grants without an ordinary
    app-registration requested-permission declaration. Cost: this exception must stay
    limited to managed identities.
18. Let beta sign-in item reads return any supported event type; preserve the
    interactive default for unfiltered beta lists and v1.0's interactive boundary.
    Cost: list and item defaults differ explicitly.
19. Delay aggregate risk jobs until their evidence availability time so a future
    aggregate cannot hide current state. Cost: eligibility depends on the tenant's
    configured evidence clock; waiting does not consume retries.
20. Use loopback ports 51443–51446 instead of reserved 494xx/504xx ranges, with
    PostgreSQL on 55436 and checked-unused runtime subnets 10.231.78.0/24 and
    10.231.79.0/24. Reuse only a hash-matching public EXO archive after a fresh
    download differed. Cost: documented local port choices; artifact pins remain fixed.
21. Derive beta interactiveUser only from an explicitly true v1.0 isInteractive
    flag when event types are absent. Cost: this positive classification is a local
    interoperability rule; false/null never imply a workload identity type.
22. Load original MES function/type declarations and collector code while omitting
    module-level update/UI startup; record named Hawk telemetry-only no-ops.
    Cost: qualification covers the declared collector closure, not every import side effect.
23. Add the persisted next_page_429 administrative fault so genuine clients retry
    a later continuation. Cost: one additive fault kind and migration; no query
    rewriting, artificial pacing or substituted retries.
24. Run one fresh whole-branch review after the first green full suite while
    collector execution finishes. Cost: runtime findings join the same fix pass;
    completion still requires remaining gates and there is no second reviewer.
25. Build target-OR expected IDs from a hashed independent administrative audit
    snapshot, requiring exact presence of every declared fixture, because MES's
    literal predicate also returns existing out-of-window state. Cost: expectation
    source defects could conceal loss; declared-fixture equality, strict output
    checks and independent SQL regressions mitigate this.

26. Preserve the fingerprinted implementation contract exactly as qualified; record
    final completion in the verification documentation. Cost: its historical
    approval-status header remains stale if read alone; README, plan, compatibility
    and verification documents provide the current status.

## Review disposition

Four Important findings were accepted and reproduced: retention of current risky
users, old delayed risk contributions overriding newer risk, nullable collection
filters, and the combined verifier invocation. All were corrected; their six
regression cases passed, followed by 302 unit/integration tests. Two later
expectation-source tests also passed with the report checks.

No Critical or Minor findings were raised. No minor findings were deferred.

The reviewer did not judge the separately diagnosed upstream MES JSON serializer,
pending final execution gates, or deferred conditional access, proprietary risk
scoring, group licensing and additional managed-identity hosting protocols.
These exclusions do not waive an implemented API or the original output gates.

## Explicit user decision

On 2026-10-16 the user approved documenting original MES audit JSON as a known
upstream limitation while requiring API, SDK, Hawk and MES SOF-ELK success.
Original failed JSON checks remain visible; only exact lossless retrieval and
the verified original append/merge defect qualify for the exception.

A later runtime check also found native create/delete audit export rejecting
string-valued JSON null as if it were a null resource property. Three regressions
reproduced this and passed after correction. Actual resource ID nullability and
validation of non-null audit values remain unchanged. The subsequent full
suite passed 307 tests in 704.59 seconds; 26 focused report checks also passed.

The final runtime pass exposed per-event source reads during risk dismissal,
exceeding the TCP proxy timeout on the expanded retained dataset. The regression
reproduced 45 fact reads for 20 linked events; a single joined read preserves
current-generation scope, risk cutoffs, immutable originals and snapshots.
All 13 focused risk checks passed in 52.98 seconds. Full regression and rebuilt qualification
passed; no timeout or retry workaround was introduced.

Final regression: 316 tests passed in 721.14 seconds, with one existing MSAL advisory.
The final combined qualification passed all 25 runtime gates,
three native/container SDK profiles in both authorization modes, and all 20 collector acceptance
gates (14 strict passes; 6 explicitly accepted original MES audit JSON limitations). SDK/runtime
checks took 581.622 seconds; the complete run took 2842.423 seconds. The report is bound to
source, wheel, lockfile, manifest, original collector files, outputs and the running image.
