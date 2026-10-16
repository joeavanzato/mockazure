# Identity/connectivity execution record



Approved plan: superpowers/plans/2026-10-16-mockazure-identity-connectivity.md.

Execution: Native implementation with one independent whole-change review.

Base foundation: 3b659f1805f861de7dfa93e35ec64d367c20aba3.

Worktree: existing isolated foundation worktree, branch feat/identity-connectivity.

VTW was not accessed or modified during this milestone.



## Completed increments



- 3aa7ff1: pinned public client artifacts, local endpoint validation and isolated client network.

- 48cca94: relational users, applications, service principals, credentials, grants and roles.

- 947cf16: persistent encrypted signing keys, discovery and app-only OAuth.

- 950c9f2: permission-enforced Graph probes and initial real SDK/Exchange feasibility.

- e40f850: browser sign-in, S256 PKCE, consent and simulated MFA.

- 4ce5ea5: persistent device authorization, refresh handles and revocable sessions.

- e38616e: approved Exchange proxy, ARM inventory/Reader assignments, SDK and collector gates.



## Decisions and limits



- Reused the isolated worktree; M2 has separate instance files, database, Compose

  names and ports. The foundation image/database retain their M1 schema.

- Docker default address pools were exhausted. Inspected unused private subnets

  10.231.72.0/24 and 10.231.73.0/24 isolate M2; no existing network was changed.

- OAuth uses bounded standard-library URL-encoded parsing, with duplicate-field

  and body limits; no multipart parser dependency is required.

- Access-token validation receives the trusted EndpointProfile explicitly.

- Signing identity initialization is explicit. An uninitialized foundation is

  administratively ready; losing wrapping material after initialization fails readiness.

- Native and container profiles advertise reachable configured endpoints while

  retaining one canonical issuer. Host headers can select only known profiles.

- Client containers mount test scripts, pinned modules, a public CA and dedicated

  lab credentials. They cannot read the server signing/wrapping keys.

- Exchange drops custom ports during bootstrap. Internal HTTPS port 443 resolves

  this without changing host trust stores, hosts files or DNS.

- The user explicitly approved a server-generated Get-OrganizationConfig proxy

  after the real EXOModuleFile request was observed. It calls the local API with

  the Microsoft context's token and returns authorized PostgreSQL state.

- Graph Python retains its standard middleware with the custom CA transport;

  otherwise its /me placeholder is not rewritten.

- Graph PowerShell native secret acquisition failed within isolation. Explicit

  local tokens work. Invoke-MgGraphRequest additionally needs an explicit local

  Authorization header for the custom host. These are separate compatibility claims.

- MES versionCheck is a preparation-only boundary; original connection functions

  and provider cmdlets remain intact. Hawk is qualified only with local contexts

  already established. No full collector output gate is claimed.

- ARM discovery was observed using api-version 2021-01-01. Migration 007 adds

  provisioned subscriptions and explicit Reader assignments, not resource actions.

- Browser form_post uses an explicit Continue button and escaped fields, preserving

  a restrictive content-security policy without inline script.

- Cleanup retains still-live authorization codes after their browser transaction

  expires. A regression test caught and verified this retention edge case.

- Foundation live regression uses its preserved clean M1 wheel interpreter,

  preventing an implicit schema upgrade of the running foundation database.



## Evidence



Tasks 1–3 full-suite counts: 63, 74 and 98. Graph increment: 100.

Delegated/device/session increment: 107 passing tests. Focused browser, session,

profile, Exchange and ARM tests exercised real PostgreSQL and observed failing

contracts before implementation. MSAL code/device/refresh flows ran over native

HTTPS, including an API restart and revoked-session rejection.



Real isolated probes passed: Python MSAL/Graph, Graph PowerShell, Exchange module

download plus command invocation, Az.Accounts subscription discovery, original

Hawk connection functions and original MES connection wrappers. Negative probes

rejected invalid credentials and missing Graph/Exchange access; ARM omitted

unassigned subscriptions. Python and PowerShell both failed external DNS/direct

IPv4/direct IPv6 connection probes.



Final verification and independent-review results are appended after they run.



Final pre-review verification: 113 unit/integration tests passed (469.90s), plus

both acceptance gates. Ruff and git diff whitespace checks passed. Wheel/sdist

provenance and package boundaries passed. The fresh identity report contains ten

successful isolated client/isolation probes and eight successful clean-wheel,

cross-replica, concurrency, revocation, rotation, restart and identity-preservation

checks. Its source fingerprint matches the running M2 image label and its wheel

contents match the source files. The preserved M1 live verifier passed separately.

One MSAL advisory recommends form_post over query response mode; both modes are

implemented, while the scripted PKCE qualification uses a native loopback query callback.



## Independent review and response



Final review: gpt-6-astra independent read-only review of 3b659f1..fe0e7a5; five Important findings and one Minor finding. No additional review cycle.

Final: Ruling: regrade the launcher profile mismatch from Minor to Important because a bearer token can be routed to an earlier local destination — reject mismatches before credential access, require a fresh process for a new profile — cost is an explicit process restart when switching instances.

Final fix pass: discovery issuer placeholder, canonical public certificate storage, signing/retirement coordination, bounded persisted Exchange application roles, executed-client tree verification and launcher profile rejection all reproduced RED and now pass focused checks. Full suite and final live qualification pending.

Final: Ruling: broad Graph collections/OData/delta, mailbox/UAL, ARM resource actions/Activity Logs and scale targets remain later milestones — only the documented probes are supported — cost if wrong is unsupported collector operations or unqualified throughput.

Final: Ruling: full Exchange RBAC scopes stay deferred, but the implemented command requires a persisted Global Reader/Global Administrator service-principal role as well as API permission — cost if wrong is incomplete parity for other Exchange roles and mailbox scopes.

Final: Ruling: conditional access, real MFA, federation, OBO/password grants and personal accounts remain explicitly unsupported — simulated MFA is a visible local outcome — cost if wrong is inability to test those authentication/policy paths.

Final: Ruling: silent browser SSO and dynamic loopback-port matching remain excluded — sign-in is interactive and registered redirects match exactly — cost if wrong is extra sign-ins or incompatibility with clients requiring ephemeral callbacks.

Final: Ruling: native PowerShell/Exchange/Az credential acquisition, Hawk default reconnect and complete collector workflows are not qualified — explicit local token injection and original connection wrappers are distinguished in the matrix — cost if wrong is additional adapters before real collectors can run end to end.

Final: Ruling: universal immediate access-token revocation is excluded — issuance/refresh and supported resource-side checks apply, with existing access tokens otherwise valid until expiry — cost if wrong is an unrealistic revocation test expectation.

Final: Ruling: Microsoft token byte parity and full-identity metadata export are excluded — local token profile and coordinated PostgreSQL/key backup are documented — cost if wrong is incompatibility with clients inspecting opaque token details or incomplete recovery from metadata exports alone.



The independent review's six findings all enter the single fix pass after grading

by user impact. The launcher finding was raised to Important because it could

send a token to a different configured local instance. There are no deferred

minor findings. Regression evidence is retained in project-local work/review-*

logs; final suite and distribution results follow after execution.


Final: fixed common/organizations discovery — test_common_authority_metadata_preserves_issuer_placeholder RED→GREEN (both local browser authorities); suite 121/121.
Final: fixed public certificate storage — test_certificate_storage_discards_private_material RED→GREEN; suite 121/121.
Final: fixed rotation/issuance coordination — test_rotation_uses_retirement_time_after_wait and test_issuance_waits_for_inflight_rotation RED→GREEN; suite 121/121.
Final: fixed Exchange operational roles — test_exchange_bootstrap_and_command_permissions RED→GREEN including missing/wrong role, grant, revocation and tenant boundary; suite 121/121.
Final: fixed executed client integrity — test_offline_verification_checks_executed_tree (modified/missing/unexpected/sibling-version) RED→GREEN; suite 121/121.
Final: fixed launcher endpoint-profile mismatch — real pinned Test-ProfileSwitch.ps1 RED→GREEN, rejecting login and Graph mismatch before credential reads; suite 121/121.
Final post-review regression: 121 unit/integration tests passed in 464.77s; one existing MSAL query-response-mode advisory. Ruff, whitespace, wheel/sdist provenance checks passed. Fresh live qualification is running against the rebuilt source-fingerprinted M2 image.

Live requalification exposed an overbroad harness edit: delegated code/device/refresh
through common passed, but the two app-only negative cases had also been moved to
common. Restored explicit tenant authority for client credentials; the provider
implementation and its passing 121-test suite were unchanged. The corrected real
client probe and full distribution qualification are rerun below.

## Completion

Task 8: complete. Runtime qualification at 2026-10-16T17:47:42.645854+00:00 identifies 0d570c19cb5d62a9373fb05d73e206842628aa40 with source fingerprint 8b68ff943956d1f1da6f8ea3d2cd07dfa0f63e1bea26bc63309fd87cfc637ef7. All 11 isolated probes and all 8 replica/restart checks passed. Both acceptance gates passed in 3.61s. Ruff, package provenance and whitespace checks passed. The 121-test backend suite passed in 464.77s; the subsequent harness-only authority correction passed real MSAL qualification. Foundation live regression was separately preserved; M2 qualification exercises API/database identity continuity, not a separate identity-worker service.
Final: no deferred minor findings. All six independent-review findings are fixed; no second review cycle was used.
Final: pushed implementation and attached draft https://github.com/joeavanzato/mockazure/pull/2, based on feat/foundation while PR #1 remains open. Preserve worktree and both running lab deployments. VTW was not accessed or changed during M2.

<details>
<summary>Native execution ledger (historical decisions and evidence)</summary>

```text
# SDD ledger — plan: docs/superpowers/plans/2026-10-16-mockazure-identity-connectivity.md

Execution approved 2026-10-16, Native. Base 2125b22. Implementation branch feat/identity-connectivity.

Task 1: in progress. Tasks 2–8: pending.

Pre-flight 1 -> 3,4,7: EndpointProfile/ResourceDescriptor configured local origins versus audiences; consistent.

Pre-flight 2 -> 3,4,5,6: tenant-scoped identity/grants consumed by token and session services; consistent.

Pre-flight 3 -> 4,5,6: PrincipalContext and token issuance require immutable claim inputs; consistent.

Pre-flight 5 -> 6: shared session/consent and TokenResponse; consistent.

Pre-flight 1,4 -> 7: Exchange/Az bootstrap inventory must precede route implementation; unresolved transport is a feasibility gate, not a license for fake cmdlets.

Pre-flight 1–7 -> 8: version manifest + isolated live reports; preserve foundation runtime.

Ruling: reuse existing linked worktree on a new implementation branch, with distinct M2 runtime paths — skill detects existing isolation and M1 containers run installed images — risk is accidental shared runtime use, prevented by explicit config/project names.



Task 1: Ruling: Docker default address pools are exhausted; assign configurable 10.231.72.0/24 after checking all Docker subnets and host routes — no existing network is changed — risk is overlap on another host, addressed by documented environment override.

Task 1: endpoint/acquisition tests RED (13 missing-module/file failures) then GREEN; full suite 63 passed in 48.14s; Ruff clean; 8 artifact SHA256 checks pass; Python and PowerShell external DNS/IPv4/IPv6 probes all blocked. Bootstrap response contract inspected, live feasibility remains Task 4.

Task 1: complete (commits 2125b22..3aa7ff1, tests: .venv/Scripts/python.exe -m pytest tests/unit/test_identity_endpoints.py tests/unit/test_prepare_clients.py -q → 13 passed in 0.20s)

Task 2: RED 8 missing identity-unit contracts and 2 missing HTTP contracts; fixed test-client fixture lifetime before implementation. CLI RED no identity command. GREEN 10 identity tests and real HTTPS CLI 1 pass; full suite 74 passed in 73.05s; Ruff clean. Users/apps/service principals/grants/credentials are relational, secret values absent from list/CLI output; cross-tenant grant rejected by PostgreSQL.

Task 2: complete (commits 3aa7ff1..48cca94, tests: .venv/Scripts/python.exe -m pytest tests/unit/test_identity_credentials.py tests/integration/test_identity_admin.py tests/integration/test_identity_cli.py -q → 11 passed in 20.07s)

Task 3: Ruling: validate_access_token accepts an explicit keyword-only EndpointProfile — the planned signature otherwise lacks the trusted issuer configuration — cost if wrong is a caller adaptation, while implicit issuer defaults would weaken validation.

Task 3: Ruling: use bounded standard-library URL-encoded parsing rather than python-multipart — OAuth accepts no multipart uploads and duplicate/body limits belong in our parser — no additional parsing dependency is necessary.

Task 3: unit token-shape checks are integration tests in test_identity_token_validation.py so they exercise real stored signing keys. Initial four malformed UUID claims raised AttributeError; explicit type checks made all eight validation cases pass. App-only/certificate/replay suite: 16 passed; real HTTPS MSAL plus identity CLI initialization/rotation/readiness: 1 passed.

Task 3: Ruling: identity initialization is an explicit CLI operation; preexisting foundation-only instances stay administratively ready before initialization — preserve approved M1 usability while failing readiness after wrapping-key loss on an initialized instance — cost if wrong is an extra explicit initialization step.

Task 3: complete (commits 48cca94..947cf16, tests: .venv/Scripts/python.exe -m pytest -q -> 98 passed in 150.36s; Ruff clean). Full fresh output: work/task3-suite.log. Completion recorded directly to avoid redundant execution of the just-passed complete suite.

Task 4: Graph authorization matrix RED then GREEN; full suite 100 passed in 138.90s, Ruff clean. Real Python MSAL/Graph and Graph PowerShell Get-MgOrganization returned expected tenant. PowerShell native acquisition failed; explicit local token path passed. Exchange reached EXOModuleFile (501); bounded server-command bootstrap adjustment requested, gate remains incomplete. Ruling: clients mount only scripts, public CA and dedicated client credentials, never server instance keys. Separate internal port-443 proxy accommodates Exchange custom-port loss without host DNS/trust changes.

Task 5: Browser HTTP tests RED 501 then GREEN (2 passed); real MSAL authorization-code/PKCE exchange over native HTTPS passed (1 test, 30.64s). Code nonce/state, single-use redemption across API instances, disabled users, CSRF, local redirect registration, denied consent, admin scope denial and simulated MFA covered. Public and confidential clients both require S256. Ruling: form_post uses an explicit Continue button with escaped fields instead of inline auto-submit script, retaining restrictive CSP. Task 4 Exchange bootstrap adjustment explicitly approved by user.

Task 6: Device/refresh tests RED then GREEN. Cleanup regression RED (live code removed after browser TTL) then GREEN with code-expiry retention. Actual MSAL device authorization across native API restart, silent refresh and revoked-session failure passed. Full suite 107 passed in 178.91s; Ruff clean. Browser/device secrets and refresh handles are stored as SHA256 digests; sessions expire absolutely at 24h. Original refresh handles remain valid until expiry/revocation. Task 6 implementation complete, whole-milestone replica/distribution checks remain Task 8.

Task 4 complete: user-approved server-generated Exchange proxy passed actual 3.11.0 module import and Get-OrganizationConfig, backed by local HTTP and PostgreSQL. Task 7: all real SDK/collector probes passed; Python native app/code/device/refresh, Graph PowerShell explicit local app/delegated tokens, Hawk original connection functions, MES original M365/Azure wrappers, Exchange rejection and ARM unassigned inventory. Both runtime isolation probes rechecked successfully. Profile/Exchange/ARM tests RED then GREEN. Ruling: retain standard Graph Python middleware when supplying local-CA HTTPX transport; otherwise SDK /me placeholder leaks to server. Ruling: Invoke-MgGraphRequest needs explicit local Authorization header on custom host; matrix distinguishes token injection from native SDK acquisition. ARM observed api-version 2021-01-01 only; new migration 007 persists subscriptions and bounded Reader assignments.

Task 8 pre-review: 113 unit/integration tests passed; 2 acceptance gates passed; Ruff/package/whitespace checks passed. tools/verify_identity.py verified a clean offline wheel, native/Docker API replicas, atomic code redemption, concurrent refresh, all-handle revocation, key rotation with old-token validity, DB/API restart, stable identity files and 10 fresh isolated probes. Foundation verifier passed using preserved M1 wheel runtime. Ruling: use the M1 interpreter for M1 live regression rather than silently upgrading its preserved database; M1-to-M2 migration is tested on a dedicated disposable test database.



Final review: gpt-6-astra independent read-only review of 3b659f1..fe0e7a5; five Important findings and one Minor finding. No additional review cycle.

Final: Ruling: regrade the launcher profile mismatch from Minor to Important because a bearer token can be routed to an earlier local destination — reject mismatches before credential access, require a fresh process for a new profile — cost is an explicit process restart when switching instances.

Final fix pass: discovery issuer placeholder, canonical public certificate storage, signing/retirement coordination, bounded persisted Exchange application roles, executed-client tree verification and launcher profile rejection all reproduced RED and now pass focused checks. Full suite and final live qualification pending.

Final: Ruling: broad Graph collections/OData/delta, mailbox/UAL, ARM resource actions/Activity Logs and scale targets remain later milestones — only the documented probes are supported — cost if wrong is unsupported collector operations or unqualified throughput.

Final: Ruling: full Exchange RBAC scopes stay deferred, but the implemented command requires a persisted Global Reader/Global Administrator service-principal role as well as API permission — cost if wrong is incomplete parity for other Exchange roles and mailbox scopes.

Final: Ruling: conditional access, real MFA, federation, OBO/password grants and personal accounts remain explicitly unsupported — simulated MFA is a visible local outcome — cost if wrong is inability to test those authentication/policy paths.

Final: Ruling: silent browser SSO and dynamic loopback-port matching remain excluded — sign-in is interactive and registered redirects match exactly — cost if wrong is extra sign-ins or incompatibility with clients requiring ephemeral callbacks.

Final: Ruling: native PowerShell/Exchange/Az credential acquisition, Hawk default reconnect and complete collector workflows are not qualified — explicit local token injection and original connection wrappers are distinguished in the matrix — cost if wrong is additional adapters before real collectors can run end to end.

Final: Ruling: universal immediate access-token revocation is excluded — issuance/refresh and supported resource-side checks apply, with existing access tokens otherwise valid until expiry — cost if wrong is an unrealistic revocation test expectation.

Final: Ruling: Microsoft token byte parity and full-identity metadata export are excluded — local token profile and coordinated PostgreSQL/key backup are documented — cost if wrong is incompatibility with clients inspecting opaque token details or incomplete recovery from metadata exports alone.


Final: fixed common/organizations discovery — test_common_authority_metadata_preserves_issuer_placeholder RED→GREEN (both local browser authorities); suite 121/121.
Final: fixed public certificate storage — test_certificate_storage_discards_private_material RED→GREEN; suite 121/121.
Final: fixed rotation/issuance coordination — test_rotation_uses_retirement_time_after_wait and test_issuance_waits_for_inflight_rotation RED→GREEN; suite 121/121.
Final: fixed Exchange operational roles — test_exchange_bootstrap_and_command_permissions RED→GREEN including missing/wrong role, grant, revocation and tenant boundary; suite 121/121.
Final: fixed executed client integrity — test_offline_verification_checks_executed_tree (modified/missing/unexpected/sibling-version) RED→GREEN; suite 121/121.
Final: fixed launcher endpoint-profile mismatch — real pinned Test-ProfileSwitch.ps1 RED→GREEN, rejecting login and Graph mismatch before credential reads; suite 121/121.
Final post-review regression: 121 unit/integration tests passed in 464.77s; one existing MSAL query-response-mode advisory. Ruff, whitespace, wheel/sdist provenance checks passed. Fresh live qualification is running against the rebuilt source-fingerprinted M2 image.

Task 8: complete. Runtime qualification at 2026-10-16T17:47:42.645854+00:00 identifies 0d570c19cb5d62a9373fb05d73e206842628aa40 with source fingerprint 8b68ff943956d1f1da6f8ea3d2cd07dfa0f63e1bea26bc63309fd87cfc637ef7. All 11 isolated probes and all 8 replica/restart checks passed. Both acceptance gates passed in 3.61s. Ruff, package provenance and whitespace checks passed. The 121-test backend suite passed in 464.77s; the subsequent harness-only authority correction passed real MSAL qualification. Foundation live regression was separately preserved; M2 qualification exercises API/database identity continuity, not a separate identity-worker service.
Final: no deferred minor findings. All six independent-review findings are fixed; no second review cycle was used.
Final: pushed implementation and attached draft https://github.com/joeavanzato/mockazure/pull/2, based on feat/foundation while PR #1 remains open. Preserve worktree and both running lab deployments. VTW was not accessed or changed during M2.

```

</details>
