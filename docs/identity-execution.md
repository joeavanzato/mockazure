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
