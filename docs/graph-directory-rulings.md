# M3A decisions and costs

This is the complete execution-ledger ruling list, in decision order. Review findings and verification are recorded in [the execution record](graph-directory-execution.md).

1. Use a new Git worktree at D:/projects/mockazure/.worktrees/graph â€” the existing worktree is the running M2 source, and the native projectless worktree tool cannot honor the explicitly mandated D: location â€” cost if wrong: separate worktree lifecycle must be maintained manually.

2. Proceed from the approved M2-based plan while integration PR #3 remains open â€” no new implementation depends on its merge graph, only the verified M2 tree; final implementation PR targets main after integration â€” cost if wrong: extra integration coordination, no source loss.

3. Rename the unit module to test_graph_query_parser.py because the planned identical unit/integration filenames collide under existing pytest import mode; changing global import behavior would disturb M2 tests. Cost: command/path deviation only.

4. A deterministic HMAC child handle keyed by the predecessor makes replay produce the identical nextLink without storing raw bearer handles. The issue_cursor interface adds an optional predecessor; cost if wrong is a cursor compatibility change.

5. Shared advisory transaction locks coordinate readers with exclusive cleanup without serializing readers; cost is cleanup waiting on long readers.

6. Versioned relationship paging and snapshot-bound expansion finish in Task 5 alongside relationship writes; current Task 4 verification covers object collection pages. Cost is a temporary development dependency, not a released limitation.

7. Bring the bounded JSON parser forward from Task 7 because writes need the same input boundary; cost is a shared component introduced earlier.

8. Split public version recording from outbox recording so one atomic deletion can update affected parent-group versions while retaining one evidence record per revision. Public property differences and relationship facts are immutable in that record, independent of history retention; cost is a richer evidence fact schema before M3B.

9. Older test fixtures that bypass domain writes now seed matching relationship versions explicitly; production has no fallback from historical reads to live edges. Cost is fixture maintenance.

10. Extend DeltaRequest with token-kind and configured origin so the shared reader can validate skip/delta semantics and construct local state links. Cost is an internal interface extension.

11. After cursor cleanup, unavailable well-formed delta handles trigger full resynchronization; their prior select is no longer recoverable. Existing expired metadata preserves select in Location. Malformed handles and available-state binding mismatches remain errors. Cost is a broader initial resync payload after cleanup, still constrained by current permissions.

12. Preserve the approved local permanent-deletion profile (reason=deleted, no restore API), although Microsoft's restorable user deletion examples use reason=changed. Retained rows are internal history only. Group delta follows Microsoft's documented omission of implicit member removals caused by object deletion. Cost is an explicit user-deletion compatibility boundary until a restorable-object profile exists.

13. Add a dedicated admin graph router for fault controls instead of expanding the existing identity router. Cost is one small additional module.

14. Use HTTPX ASGITransport for internal dispatch to retain the complete request/auth/error/quota path without network I/O. Bound response bodies to 8 MiB per item/32 MiB per batch; cost is explicit rejection of unusually large batches, which callers can split.

15. Version-one datasets form a closed user/group graph, with no credential or service-principal restoration. An export with SP owners fails explicitly rather than dropping references; M3C will extend the format. Cost is that such tenants cannot use the A public dataset exporter yet.

16. Preserve dataset provenance in the import outbox record (source/description/seed/generator scalar fields) and export the latest import provenance at the snapshot. This avoids adding a second metadata store; cost is that mixed imports report the latest dataset provenance rather than per-object lineage.

17. Round-trip ID preservation is verified in a fresh database because identity object IDs remain globally unique within an instance. Cost is no same-instance clone with unchanged IDs; collisions fail atomically.

18. Support validated generated SDK type annotations and function-call delta aliases. Cost is a few equivalent accepted wire forms, with no new resource or mutation scope.

19. Use the genuine PowerShell generated delta cmdlet for bootstrap, then its Invoke-MgGraphRequest for opaque links because this pinned cmdlet exposes no delta-link parameter. Cost is a documented client workflow detail rather than generated-only traversal.

20. Service-principal owner fields remain live in A because no supported A route mutates those projected fields. M3C must version them before adding writes. Cost if wrong: future SP changes could violate relationship snapshot replay.

21. Audit projection, collector evidence, conditional access and broader security administration remain explicitly M3B/C. Cost: A cannot validate those workflows.

22. Issued access-token permission claims retain the parent design's semantics; grant changes do not rewrite existing token claims. Current subject/role checks remain. Cost: grant revocation is not immediate token-permission revocation.

23. Functional 5,000-user qualification does not establish 100,000-user or million-evidence-record throughput. Cost: scale targets need the later measured gate.

24. Fidelity claims stay within the documented contract and public references; real-tenant testing remains separately scoped. Cost: unmodeled service differences can remain.

25. Publish the completed M3A change as a draft PR targeting main while M2 integration PR #3 remains open. This preserves the approved target and makes the work reviewable without merging for the user. Cost: the draft comparison includes prerequisite M2 changes until #3 merges; mark ready after that integration.

Deferred minors: none. All four Important review findings were fixed with reproducing tests.
