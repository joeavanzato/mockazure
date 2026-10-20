# Living tenant and Graph hunting qualification

Runtime candidate: `bab9eb9571ae901c4206b828c6807c929f450842`.
Final documentation and recorded reports land after this runtime commit.
All runtime comparisons use its package-byte fingerprint.

This program adds connected business content, finite public API activity, native
operation evidence, identity/access lifecycle exercises and Graph hunting across
20 source-pinned tables. Start with [business profiles](../living-tenant-profiles.md),
[finite activity](../finite-activity.md), [evidence mappings](../living-tenant-evidence.md),
[hunting access and limits](../hunting-data.md), and the [24-query cookbook](../hunting-cookbook.md).

## Final results

The native API and worker ran from the installed candidate wheel on Windows,
with a fresh database on the owned PostgreSQL 18 instance. A separate fresh Compose
project ran the candidate Docker image with its own PostgreSQL volume. An installed
source archive ran a third native API/worker and database.
Each final lab used one worker. The large lab initially used two workers on the
preceding candidate; it resumed with one worker on the final candidate.

| Gate | Native wheel | Compose |
| --- | --- | --- |
| Content | Passed | Passed |
| Native evidence and denied-call collection | Passed | Passed |
| Identity lifecycle, PIM and Azure access removal | Passed | Passed |
| All 24 hunting cookbook queries, original Graph SDK and PowerShell | Passed | Passed |
| Foreign-tenant workspace/name isolation | Passed | Passed |
| Population replay with zero creations | Passed | Passed |

Content checks use actual IDs and document bytes across Graph mail/drives/Teams,
Blob/File, Table/Queue and original Exchange mailbox commands. Evidence checks
collect the generated source IDs through Graph, Exchange, the activity feed, ARM
and Logs; imports remain separate from native delivery. Identity checks include
allowed-to-denied transitions after disablement, deletion/restoration, credential
rotation, PIM deactivation and Blob-role removal.

Machine-readable results:
[native](living-tenant-native.json), [Compose](living-tenant-compose.json),
[source archive hunting](living-tenant-sdist.json), [large preset](living-tenant-large.json),
and [build/regression summary](living-tenant-build.json).
The source archive also passes all hunting client/cookbook checks. Small-preset
query IDs and returned row counts match across all three installed distributions.

| Runtime / preset | Users | Hunting rows | Payload bytes | Population seconds | 24-query seconds | Client peak bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Wheel / small | 20 | 400 | 208,382 | 107.328 | 1.484 | 360,353,792 |
| Compose / small | 20 | 400 | 208,382 | 69.765 | 1.250 | 361,226,240 |
| Source archive / small | 20 | 400 | 208,382 | 101.000 | 1.344 | 204,468,224 |
| Wheel / large | 2,500 | 100,000 | 52,309,018 | 3,564.559 | 26.578 | 204,529,664 |

Population time includes planning and application, not replay. The large run's
elapsed population time also includes its recorded conflict, wait and recovery. Query time
is the sum of the 24 measured client HTTP calls. Payload bytes sum PostgreSQL's
textual JSON representation of hunting rows; they are not physical database size
and exclude indexes and other tenant services. Peak memory
is the Windows qualification client's measured peak working set; it excludes API,
worker and PostgreSQL processes. These are shared-host qualification observations,
not isolated throughput benchmarks or capacity guarantees.

The large population initially stopped during owned-device import when PostgreSQL
rejected a concurrent serializable transaction (`40001`). Its first resume reached
17,670 verified records, then exposed repeated whole-inventory validation: importing
100 Intune relationships validated the scope 100 times and exceeded the worker's
18-second operation bound. Both failed transactions rolled back.

The final candidate groups relationship imports by scope and validates each group
once, while preserving append-only behavior and atomic rollback. A regression test
reproduced the 100 validations before the fix; all 13 relationship/workload/recovery
tests passed afterward. The original frozen large run resumed on the corrected
installed wheel, rechecked its durable receipts, completed all client/cookbook/scale
checks and performed a separate zero-creation replay. The large report records both
interruptions and the candidate transition. This qualifies recovery and final-state
behavior; it is not an uninterrupted fresh-build throughput measurement.

## Reproduce

Use a fresh owned native or Compose instance and the locked Python `clients`
dependency group. Prepare the unchanged vendor clients from the repository's pinned
manifests; the qualification commands verify archive and extracted-tree hashes.
All instance URLs and trust roots remain local.

```powershell
python -m tools.build_release --commit bab9eb9571ae901c4206b828c6807c929f450842 `
  --output work/release/living-d4-batched
python -m tools.verify_living_tenant --config NATIVE_CONFIG --phase all `
  --scale small --distribution native --report work/d4-batched-native-all.json
python -m tools.verify_living_tenant --config COMPOSE_HOST_CONFIG --phase all `
  --scale small --distribution docker --report work/d4-batched-compose-all.json
python -m tools.verify_living_tenant --config NATIVE_CONFIG --phase hunting `
  --scale large --distribution native --report work/d4-batched-native-large.json
python -m tools.verify_living_tenant --config SDIST_CONFIG --phase hunting `
  --scale small --distribution native --report work/d4-batched-sdist-hunting.json
```

`build_release` requires the exact committed source tree and a new output directory.
It builds wheel/source archives twice, verifies reproducible bytes, installs the
wheel and compares package assets with Docker. Source-archive installation and
actual runtime queries were additionally exercised here. Archive hashes and the
exact Docker image ID are in the build summary.

| Client | Verified version |
| --- | --- |
| ExchangeOnlineManagement | 3.11.0 |
| Microsoft.Graph.Identity.Governance | 2.35.1 |
| Microsoft.Graph.Security | 2.35.1 |
| azure-data-tables | 12.7.0 |
| azure-storage-blob | 12.27.0 |
| azure-storage-file-share | 12.23.0 |
| azure-storage-queue | 12.15.0 |
| httpx | 0.28.1 |
| msgraph-sdk | 1.64.0 |

On Windows, original Exchange and Graph PowerShell clients run in the pinned Linux
PowerShell image using the established CA-validating localhost relay. The native
API and worker remain native. No client module source or OS trust store is changed.
The approved bounded Exchange bootstrap remains the documented emulator contract.

## Regression, browser and review

**2,101 unit/integration tests passed**, across the final full run and four separately enabled opt-in cases. Before that final run, two legacy assertions incorrectly counted new failed-operation diagnostics as successful writes, and a direct authorization test helper omitted the middleware request ID (four cases). Both assertions and the helper were corrected. All three affected modules were rerun successfully; the preceding run's six failures remain visible in the build summary. Those corrections changed only tests; the final runtime additionally fixes relationship batching. The standard run skipped four explicitly opt-in cases; all four subsequently passed with their required flags enabled (256 MiB bounded-content storage and Python/TypeScript/.NET structured-service recovery). Every collected case was therefore executed successfully. Browser and acceptance gates also passed.

The full unit/integration inventory ran after the final review fix, partitioned by
test module into independent processes with per-test databases and separate
temporary directories. No test files were omitted. The coverage checker verified
317 existing cloud operations, and offline hunting acquisition verified all 20
hash-pinned table references.

The real browser checks exercise finite activity/cancellation/resume and Hunting:
named-identity authorization, visible permission errors, typed results, literal
rendering of HTML-like content, JSON export availability and related-record browsing.
Screenshots were visually inspected.

One independent whole-program review examined the five plan risk areas and the
broader implementation. Its important finding was reproduced: a concurrent
hunting-visibility write could survive deletion and restore. The final fix shares
the security transaction lock with deletion. The interleaving test now proves
the competing grant is rejected and restoration grants no access. Eight focused
authorization/console tests pass; the independent review reported no minor findings.

One reporting-only limitation was subsequently observed: the shared PIM helper
still emits its legacy `api_runtime: native` value inside the Compose identity
subreport. The top-level `distribution: docker`, recorded Compose configuration
and candidate image identify the actual API runtime. Raw reports retain that
observed field; correcting the helper label is deferred. It does not alter the
executed allowed/denied client checks.

The final native API log also recorded a Storage stream-finalization database
error (`another command is already in progress`) while committing its read
session after streaming. Download clients passed their exact byte/hash checks,
and subsequent workflow gates passed. `TransactionalStream` and `stream_content`
are unchanged from `main`; the only Blob-route change in this program normalizes
the lease-operation evidence label. The cleanup error's trigger is not fully
diagnosed; unchanged stream code does not establish when the failure first
appeared. It remains an explicit follow-up. These results do not
qualify download-disconnect/cancellation cleanup or claim error-free server logs.

Hosted CI is **blocked**, not passed. [Run 39495443035](https://github.com/joeavanzato/mockazure/actions/runs/39495443035)
did not start jobs because GitHub reported an account billing/spending restriction.
Local results above do not claim a successful hosted run.

## Boundaries

- Bounded Graph v1.0/KQL/permission profiles, not complete Microsoft service behavior.
- Process, network, registry, click and detector telemetry remain labeled fixtures.
  Native hunting publication currently maps supported workload/mail audits to
  CloudAppEvents; it does not synthesize detector verdicts.
- No incident updates, response actions, chaos engine or always-on activity scheduler.
- Entra PIM is tenant-scoped and uses real wall time for authorization; evidence
  clock changes do not extend credentials or activate grants.
- Guest delivery/redemption is local. Restore never revives removed authentication
  authority. Private signing-certificate import remains unsupported.
- `Get-Mailbox` collection and `Get-EXOMailbox -Identity` are the qualified Exchange
  variants; unscoped compiled `Get-EXOMailbox` remains explicitly unsupported.
- The previously accepted original MES concatenated-JSON output defect remains an
  upstream limitation. No new collector failure was waived.

All runtime traffic stayed inside the lab. Existing root-stack state, VTW,
other project data and Docker networks were preserved. Final Compose used verified
unused explicit subnets because Docker's automatic allocation pool was exhausted.

## Implementation decisions and review exclusions

- Skill task-start/task-done helpers assume numbered single-file task headings; the approved program uses A1–D4 across four phase files. Keep one program workspace and extract each exact phase brief here with names A1–D4, recording BASE and final test commands manually — preserves approved task identities and avoids four competing ledgers — cost if wrong: bookkeeping mistakes, mitigated by per-task commits/logs.

- Use a separate namespace/generation-aware binding table because existing ResourceBinding has one ID per logical key and no service/generation columns — preserves old profiles without ambiguous IDs — cost if wrong: redundant metadata, tested with namespace/generation isolation.

- Reference six existing cloud catalogs by pinned SHA256 instead of duplicating 317 operation definitions — keeps one authority per service — cost if wrong: changes to those catalogs require deliberate inventory hash updates.

- Introduce the minimal organization-projects layer during A3 rather than A5 — both new collaboration layers must resolve and run now — cost if wrong: shared base setup lands earlier, mitigated by explicit user-step override and fresh-namespace documentation; A5 extends this same layer.

- A4 omits chat-member PATCH and uses the documented user-scoped chat softDelete/undoSoftDelete variants — pinned Graph v1.0 has no chat-member PATCH and source fidelity governs the generic plan wording — cost if wrong: narrower operation coverage; source refs and explicit unsupported status preserve visibility.

- A4 delegated member management requires authoritative ownership except self-removal, and last-owner removal fails atomically — bounded local policy avoids implying support for every Teams policy configuration — cost if wrong: some real-tenant workflows are stricter locally; documented in teams-native-workflows.md.

- A4 supports the declared permission workflow through native site selected-app grants; createLink remains explicitly unsupported — existing A3 recipes require site authority, not anonymous/organization sharing links — cost if wrong: narrower sharing coverage; COVERAGE and the operation inventory expose the boundary.

- A4 single-file copies finish atomically before returning 202 and a persisted caller-bound 303 monitor — bounded 8 MiB workflows need completion semantics but not an artificial queue delay — cost if wrong: consumers cannot test prolonged copy progress here.

- A4 extends named identities with Graph delegated actors — existing named identities were app-only and could not support the approved ordinary Teams activity — only credentialless imported users may be bootstrapped; passwords remain encrypted and actual local PKCE/login/consent/policy endpoints issue tokens. Existing credentials are never replaced. Cost if wrong: an extra explicit fixture setup requirement for activity; no MFA success is fabricated.

- A5 records fixture identity aliases under lab-identity, canonical resource IDs under arm, and public action identities under their declared service. The deviceManagement routing root is not a resource parent. Normalized fixture identities handle relationship composite IDs and ARM casing. Cost if wrong: narrower cross-service identity linkage; public app ownership is independently verified.

- ActivityBatch also names recipe_step, an existing supported public-action step in the chosen template. Reuse the template dependency/setup graph, replace that one step with a bounded indexed action stream, and reject unsupported step kinds during preview. This resolves the plan reference to a selected recipe without assuming a hidden default; no arbitrary method/path/origin is accepted. Cost if wrong: users select a separate run per action recipe rather than a heterogeneous activity mix.

- On Windows, native-server qualification runs the unchanged Exchange 3.11.0 client in the existing pinned Linux PowerShell image. Its process trusts only the mounted lab CA and a container-local raw TCP relay preserves localhost TLS to the native server. No OS trust-store edits or module patching. API/worker remain native and report identifies the client runtime. Cost if wrong: Docker is required for this client gate on Windows; documented explicitly.

- The official Gallery Exchange archive no longer matches the repository pin. Reused an earlier locally cached archive only after verifying its original SHA256; all extracted client trees remain unchanged. No pin update or unverified substitute.

- Qualification uses Get-Mailbox for complete mailbox enumeration and compiled Get-EXOMailbox -Identity for canonical readback. The latter unscoped private route returns 501 and was never needed by this phase; COVERAGE now separates these variants. No fabricated compiled-client pass or module replacement.

- redirect targets must be configured local browser origins; local redemption is explicitly a MockAzure control-plane operation, not simulated external Microsoft identity proof. No outbound invitation delivery. Corrected stale catalog assertion for the already-supported import-owned device family.

- retain the existing public RSA AsymmetricX509Cert/Verify profile. X509CertAndPassword/Sign private certificate import is explicitly unsupported; do not expand secret-at-rest scope or store private keys in profiles. Public recipes use caller-held keys and real API calls.

- distinguish protected-target classification from caller effective authority so disabled administrators remain protected without granting access. Tenant P2 inventory gates PIM; per-user license compliance and broader Governance licensing remain explicit limitations. Existing direct roles survive PIM expiry. Canceled request collection cleanup retains historical audit facts.

- new profile layers deliberately separate operator and subject names when stacked. Eligibility profile does not fake activation or credential bootstrap. Use the existing local guest control only for local redemption. Certificate proof/real-token rotation remains covered by C4 integration; native C6 records original-client password rotation separately.

- retention/reset are explicit authenticated tenant-local controls, not automatic expiration. Imported history retains fixture attribution even if exported from native observations. One MiB rows, 10000 rows/eight MiB direct import, 500 rows/eight MiB export pages; generic browser retains its 1000-mutation bound.

- local service entitlement switches and delegated View Data/device sets are explicitly bounded emulation, not complete Microsoft licensing or unified RBAC. Native 30-day retention, optional local Sentinel-UEBA 366-day profile; no arbitrary workspace tables or legacy Defender transport. Hunting visibility removal is new deletion cleanup; old final-query RED claim was premature because the re-enable setup first failed.

- Native map is deliberately limited to substantiated UAL cloud outcomes; security/host/identity sensor and detector history is fixture-only. Optional overlays identify real subjects; unobserved application/vault targets are omitted. Native publication uses the existing EvidenceState/receipt fence instead of acquiring Tenant after EvidenceState, avoiding a new lock-order inversion. Whole-program native/Compose/scale qualification remains D4.

- Deliver the connected A-D program in one PR rather than four phase PRs — the user prefers the shortest practical task chain, shared interfaces were qualified together, and one whole-program review covered the branch — cost if wrong: a larger PR to review; cohesive commits preserve separation.

- Incident mutation and chaos remain excluded by the approved scope — cost: those workflows require another environment.

- Full Defender RBAC and arbitrary Sentinel/Logs workspace access remain outside bounded hunting — cost: broader authorization/workspace workflows cannot be qualified here.

- Session revocation does not promise immediate invalidation of every standalone access token — independent current-identity/grant checks apply where implemented — cost: clients must use the documented token profile.

- Group recovery, Azure/scoped/custom PIM and approval workflows remain explicitly unsupported — cost: those workflows need a different lab implementation.

- Long-running copy progress, createLink and private signing-certificate import retain the documented bounded profiles — cost: narrower sharing/copy/certificate coverage.

- The accepted MES concatenated-JSON defect remains an upstream exception — cost: original MES JSON output remains unsuitable for direct JSON parsing; other collection gates remain enforced.

- Hosted CI billing is a blocker, never a pass — cost: hosted verification requires the account owner to restore runner eligibility.

- The review's final qualification and report requirements were retained and completed before delivery — no acceptance waiver.

Reviewer exclusions were retained deliberately: incident mutation/chaos, full
Defender RBAC/arbitrary workspaces, universal standalone-token invalidation,
group recovery/Azure or scoped/custom PIM/approval workflows, long-running copy
progress/createLink/private certificate import, and the accepted MES output
defect. Each is a documented bounded profile or explicit exclusion; the cost is
that clients needing those behaviors must use another environment. Hosted billing
was treated as a blocker, never as a pass. The review's then-pending final reports
and regression/scale checks are completed by the evidence above.

One combined PR carries the cohesive program because its shared binding, evidence,
identity and hunting interfaces are qualified together. The earlier launcher PR
#34 is already merged. No automatic merge or root-checkout update was performed.

