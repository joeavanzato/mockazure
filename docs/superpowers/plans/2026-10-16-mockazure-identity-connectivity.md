# MockAzure Milestone 2: Identity and Connectivity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect real Microsoft SDKs and the two selected collector families to local, persistent Microsoft authentication with enforced permissions and no real-service fallback.

**Architecture:** Extend the existing modular ASGI application with identity, OAuth and authorization modules backed by PostgreSQL. Use small authenticated provider operations to prove connections before broad resource implementation. Run pinned clients in an isolated environment; connection adapters may configure endpoints and authentication only.

**Tech Stack:** Existing CPython 3.12, FastAPI, SQLAlchemy 2/psycopg 3, Alembic, PostgreSQL 18, cryptography, Typer, HTTPX and pytest. Add PyJWT with cryptography for JOSE validation and python-multipart for bounded form parsing; resolve stable compatible versions and commit uv.lock during execution. Acquire and pin MSAL Python, Microsoft Graph Python SDK, Microsoft.Graph.Authentication, Microsoft.Graph.Identity.DirectoryManagement, Az.Accounts, ExchangeOnlineManagement, Hawk and Microsoft Extractor Suite separately from application dependencies.

**Spec:** ../specs/2026-10-16-mockazure-design.md (accepted), especially sections 6, 7, 11, 13 and milestone 2 in section 14.

**Status:** Approved 2026-10-16; Native implementation in progress. Tasks 1–3 complete.
**Execution preference:** Native, carried forward from the user's selection: main agent implements; one independent whole-change review at the end.
**Starting point:** Foundation commit 3b659f1805f861de7dfa93e35ec64d367c20aba3. PR #1 remains unmerged as of planning. Do not start from the documentation-only remote main or merge PR #1 implicitly.

## Global Constraints

- Never edit, generate files, install dependencies, run tests or initialize tools inside E:\projects\vtw.
- All new project files, research downloads, generated credentials, data, exports and test output belong under D:\projects\mockazure.
- Runtime and integration tests never contact a real Microsoft tenant. There is no upstream fallback or transparent forwarding to Microsoft.
- Official documentation and public client source are acceptable research inputs. Dependency acquisition is a separate preparation step; compatibility runs operate with external service access blocked.
- PostgreSQL is the system of record; there is no SQLite alternative.
- The native Python/CLI installation has the same functionality and requires PostgreSQL.
- No production-host DNS override is the default installation mechanism.
- Default published ports bind locally. Trust-store installation is explicit; per-client CA configuration is preferred when supported.
- Ordinary shareable dataset exports omit credentials, private keys and session secrets.
- Licence: Apache-2.0.
- Commit coherent changes frequently. Never force-push over unknown history.
- No VTW integration or migration. Do not access its source again for this milestone.
- Preserve the running foundation's instance files and database. M2 validation uses distinct project-local instance paths, Compose project names and available ports.
- Do not install CA certificates into Windows trust stores or change global PowerShell profiles, DNS, hosts files, Git configuration or machine firewall rules.

## Review Focus

1. Cross-tenant references and resource-audience confusion: composite ownership constraints and service-specific token validation must prevent leakage (Tasks 2, 3, 4).
2. Parallel code redemption, device polling and refresh use on different API replicas: outcomes must follow persistent transactional state, including revocation (Tasks 5, 6).
3. Rotation/restart with old access tokens: replicas must agree on active signing keys and retained verification keys; private material must never enter JWKS, exports or logs (Tasks 3, 8).
4. Malicious redirect URIs, duplicate form fields, CSRF and Host/forwarding headers: reject before redirecting or issuing credentials, without weakening M1 boundary tests (Tasks 1, 5).
5. Client authority discovery, telemetry and automatic fallback: isolation must hold for PowerShell/.NET as well as Python, with a failed isolation probe making qualification fail (Tasks 1, 7, 8).

## Scope, order and acceptance

The approved milestone already includes both authentication modes. Keep its scope: identity/connectivity with core authorization, not full Graph, Exchange or ARM coverage.

Execution order: client contract/isolation discovery; identity administration; keys/app-only OAuth; protected Graph probes and an early real Exchange bootstrap attempt; delegated authorization; device/refresh sessions; completed SDK/collector connection qualification; distribution/restart/review.

A failed Exchange connection experiment is useful evidence but is not milestone completion. Record the exact blocked request/transport and stop dependent compatibility work for an explicit design adjustment if it cannot be supported by endpoint/auth configuration. Continue independent identity work already authorized by this plan. Never substitute fake Connect-ExchangeOnline or canned provider cmdlets.

M2 exit conditions:
- Real secrets and registered certificate assertions are checked; replay, expiry and wrong client/tenant/audience fail.
- App-only, authorization-code/PKCE, device-code and refresh flows work against local discovery/JWKS.
- Local users, apps, service principals, grants, consent, sessions and signing identity survive restarts.
- Supported resource requests check token identity, app roles/delegated scopes and applicable user access.
- Python SDK and PowerShell SDK perform real authenticated requests and handle denied access.
- Hawk and MES establish their actual required Graph/Exchange connection contexts; MES ARM connection routing is demonstrated locally. Pin precisely which connection wrappers and dependencies were tested.
- External service access is blocked during client runs. No compatibility claim rests only on source inspection.
- Broad collector output gates remain M3-M5; M2 does not claim collection workflows complete.

## File and interface map

Paths are repository-relative. Existing administration, tenant and job modules remain in place.

| Files | Responsibility |
| --- | --- |
| src/mockazure/endpoints.py; config.py | Validated service origins, authority profiles, resource aliases |
| src/mockazure/identity/types.py, models.py, repository.py, credentials.py | Persistent users/apps/service principals/grants and credential verification |
| src/mockazure/identity/keys.py, tokens.py | Encrypted signing-key storage, rotation, JWT issuance/verification |
| src/mockazure/oauth/errors.py, forms.py, discovery.py, routes.py | Microsoft-shaped protocol boundaries, bounded OAuth form parsing |
| src/mockazure/oauth/authorization.py, device.py, sessions.py | Authorization transactions, device state, refresh and browser sessions |
| src/mockazure/oauth/pages.py | Minimal escaped local login, consent, device and simulated-MFA forms |
| src/mockazure/authorization/catalog.py, evaluator.py | Explicit service permissions and operation requirements |
| src/mockazure/providers/graph/probes.py, errors.py | Organization and signed-in-user connection probes |
| src/mockazure/admin/identity_routes.py; commands/identity.py, clients.py | Authenticated provisioning, revocation, rotation and client settings |
| src/mockazure/db/alembic/versions/003_identity.py, 004_signing_keys.py, 005_oauth_state.py, 006_device_outcomes.py | Identity/key schema, then protocol/session schema |
| src/mockazure/admin/app.py; cli.py | Router and command composition; provider-specific exception boundaries |
| integrations/manifest.json, contracts/**, powershell/**, python/** | Pinned client contracts, launchers and executable probes |
| compose.clients.yaml; tools/prepare_clients.py, verify_identity.py | Acquisition, isolated qualification and sanitized evidence report |
| tests/unit/test_identity_*.py; tests/integration/test_identity_*.py; tests/acceptance/test_identity_distribution.py | Focused unit, real PostgreSQL and live distribution checks |
| docs/identity.md, docs/compatibility.md, docs/capabilities.md | Supported protocols, limits, versions and reproducible instructions |

Add package __init__.py files as needed. Define provider bootstrap files only after Task 1 identifies the actual pinned requests; record that bounded file/route list in integrations/contracts before implementing it. Do not invent Exchange bootstrap based on the legacy mock.

## Shared protocol and state decisions

These are MockAzure's initial supported profile, not claims that all Microsoft tenants use identical defaults.

- Advertise configured HTTPS login/Graph/Exchange/ARM origins separately. Browser and container configurations may use reachable aliases but share one canonical issuer. Metadata uses configuration, never untrusted Host or forwarded headers.
- Default local configuration may share one HTTPS origin for distinct provider paths; clients requiring distinct origins get explicit project-local aliases/SANs in the isolated client network. Do not rewrite production DNS on the host.
- Accept tenant GUID or registered tenant domain. Common/organizations discovery is supported; app-only token issuance requires a concrete tenant. Delegated common/organizations routing resolves a known local user and registered local service principal, never an external account.
- V2 endpoints: GET /{tenant}/v2.0/.well-known/openid-configuration, GET /{tenant}/discovery/v2.0/keys, GET /{tenant}/oauth2/v2.0/authorize, POST /{tenant}/oauth2/v2.0/token, POST /{tenant}/oauth2/v2.0/devicecode. GET /common/discovery/instance returns local authority metadata only for configured authorities.
- Task 1 pins any necessary v1 discovery/token compatibility for Az/Exchange. Support resource-parameter parsing and v1 issuer/claim differences only where demonstrated; do not label v2 responses as v1.
- Register resource identifiers/aliases explicitly for Graph, Exchange and ARM. A known resource identifier is a claim target, not a network destination. Unknown resources and mixed-resource scope requests fail.
- Access and ID token TTL: 3,600 seconds; validation clock tolerance: 60 seconds. Authorization code: 300 seconds, single use. Device code: 900 seconds, polling interval initially 5 seconds. Browser/refresh sessions: absolute 24-hour lifetime. Use UTC wall clock with injected clocks only in tests.
- ID tokens are issued only with openid; profile/email claims only as supported by requested scope and stored user data. Resource endpoints reject ID tokens, even when signature and issuer are valid.
- Registered client secrets and user passwords use salted scrypt verification with bounded input and work factors. Generate secrets explicitly, return only once; storage/API list responses expose hashes/metadata only internally, never secret values. Certificate registration stores public certificates only.
- Client assertions support explicitly allowed RS256 and PS256, registered certificate thumbprints, exact client iss/sub, endpoint audience, bounded validity and durable unique (tenant, client, jti) replay records until expiry. No alg=none, algorithm confusion, arbitrary key URLs or unregistered x5c trust.
- Issue access tokens using persistent RS256 keys and kid. Store encrypted private keys in PostgreSQL using a project-owned instance wrapping key file generated explicitly by initialization. Runtime mounts read that wrapping key; all replicas use DB key state. Keep TLS and OAuth keys separate.
- Rotation is explicit, transactionally serialized; publish old public keys through the last issued token's expiry plus skew. Do not delete verification keys while valid tokens require them. Missing/wrong wrapping material fails readiness/issuance clearly; do not regenerate automatically.
- Grant/secret changes govern subsequent issuance. Existing access-token claims remain usable until expiry unless a separately documented resource rule denies the operation; no claim rewriting or invented universal immediate revocation. Revoking a delegated session blocks refresh/browser reuse. Refresh requests reevaluate enabled user/client, current grants and consent.
- Refresh tokens are opaque hashed handles, bound to tenant/client/user/session. Renewal returns a new handle without making the previous unexpired handle single-use; all handles share the session's original absolute expiry and revocation state. Concurrent refresh is supported without falsely treating it as theft.
- Browser authentication uses HttpOnly Secure SameSite cookies, CSRF tokens and persistent server-side transactions. Authorization codes and device approvals bind client, tenant, scopes, redirect and PKCE. Public-client authorization-code flow requires S256 PKCE.
- Redirects match registered local HTTPS URIs or documented native loopback HTTP callbacks. Implement loopback port matching only as required by the pinned client contract; disallow wildcards, userinfo, fragments and external callback destinations. Invalid callbacks get local errors, never redirects.
- User consent can grant configured user-consentable scopes; admin-required permissions require an explicitly authorized local administrator or management provisioning. The admin bearer token never acts as a Microsoft access token.
- Simulated MFA is visibly a lab action. Record its outcome in the session; configurable CA policy evaluation belongs to M3. Do not claim CA enforcement just because a user can complete an MFA screen.
- OAuth errors use OAuth JSON/redirect envelopes; Graph errors use Graph code/message/innerError with request identifiers. Do not route protocol errors through the administrative 422 envelope. Secrets, passwords, assertion strings, codes, tokens and cookies must be absent from logs.
- M1 export schema remains explicitly tenant-metadata-only in M2. Do not silently imply identity backup; document full DB plus wrapping/TLS files for operational backup. Test that ordinary exports still contain no identity secrets.

## Task 1: Pin client connection contracts and isolation

**Files:** Create integrations/manifest.json, integrations/contracts/connections.md, tools/prepare_clients.py, compose.clients.yaml, tests/unit/test_identity_endpoints.py; create endpoints.py and extend config.py.

**Interfaces:**
- EndpointProfile(login, graph, exchange, arm, browser_login): validated HTTPS bases.
- resolve_resource(alias: str, profile: EndpointProfile) -> ResourceDescriptor(service, audience, aliases).
- prepare_clients.py --output work/vendor --manifest integrations/manifest.json acquires pinned artifacts; --verify only checks hashes, with no network.
- Manifest schema_version=1; entries include source URL, immutable revision/version, SHA256, dependency graph, runtime version, intended probe and supported endpoint switches.

- [x] **Step 1: Capture exact stable client versions and source revisions.** Inspect real connection wrappers and imported dependencies; record bootstrap requests, auth version/audience, SDK authority discovery, URL construction and supported overrides. Pin release versions/commit SHAs and content hashes, never latest at test time. Include actual command names for Hawk/MES connectivity and Exchange module bootstrap in connections.md.
- [x] **Step 2: Add endpoint validation and isolation tests.** Assert `resolve_resource("https://graph.microsoft.com", profile).service == "graph"`; assert unknown aliases fail and cannot trigger HTTP. Assert credentials/query/fragment/unsupported path in service origins fail. Hostile Host and X-Forwarded-Host cannot change issuer or callbacks.
- [x] **Step 3: Run the new unit file and record expected missing-module failures.** Use .venv/Scripts/python.exe -m pytest tests/unit/test_identity_endpoints.py -q.
- [x] **Step 4: Implement endpoint types/settings and prepare the pinned clients.** Preserve existing M1 config defaults. Keep package caches/module paths under work/vendor or .runtime; acquisition runs separately from qualification.
- [x] **Step 5: Prove runtime isolation before authentication.** Client containers have only the internal network, local DNS aliases and per-container CA trust. Block public IP as well as hostname access, including IPv6 when enabled; record failed external DNS/direct-socket probes for Python and PowerShell/.NET. No inherited proxy may bypass this. A client unable to execute within this boundary is explicitly unqualified.
- [x] **Step 6: Verify endpoint tests and artifact hashes; commit.** Commit message: build: pin isolated Microsoft client connection contracts.

## Task 2: Persistent identity administration and permission catalog

**Files:** Create identity/types.py, models.py, repository.py, credentials.py, authorization/catalog.py, admin/identity_routes.py, commands/identity.py, migration 003_identity.py, tests/unit/test_identity_credentials.py, tests/integration/test_identity_admin.py. Extend db model registration, app.py, CLI and AdminClient.

**Interfaces:**
- UserCreate(UPN, display_name, password, enabled); ApplicationCreate(display_name, client_mode, redirect_uris, required_permissions); CredentialCreate(kind, public_certificate, expires_at); GrantCreate(client_id, resource, kind, permissions, user_id). Define validated request types in identity/types.py; secret creation generates a value rather than accepting real credentials.
- PrincipalContext(tenant_id: UUID, client_id: UUID, subject_id: UUID | None, token_kind: Literal["application","delegated"], audience: str, permissions: frozenset[str], session_id: UUID | None).
- IdentityRepository(session: AsyncSession): create_user(tenant_id, UserCreate), create_application(tenant_id, ApplicationCreate), add_credential(tenant_id, client_id, CredentialCreate), set_grant(tenant_id, GrantCreate), revoke_credential(tenant_id, credential_id), set_enabled(tenant_id, object_id, enabled).
- PermissionDefinition(resource, name, kind, admin_consent_required); distinct application-role and delegated-scope records. App registration, tenant service principal and permission grants are separate objects.
- Management routes under /_mock/tenants/{tenant_id}/identity: POST/GET users, applications; POST applications/{client_id}/credentials; DELETE credentials/{credential_id}; PUT/DELETE grants/{grant_id}; PATCH users/{id} and applications/{client_id} for enabled state. Bounded UUID pagination for lists.
- New credentials/password provisioning reads request body or protected input/file, not command-line arguments that expose secrets in process listings.

- [x] **Step 1: Add tests for create/list/revoke, same-tenant foreign keys, duplicate UPN/client identity and hashed credentials.** Assert wrong-tenant grant insertion fails at DB constraint level, `verify_secret(wrong, digest) is False`, list responses contain neither password nor digest, and management bearer cannot be omitted.
- [x] **Step 2: Run these unit/integration files against the dedicated PostgreSQL test service; observe failures.**
- [x] **Step 3: Implement indexed relational state and administrative commands.** Users have tenant/id/UPN/displayName/enabled/password verifier; apps have public/confidential mode, redirect allowlist and configured resource permissions; service principals and grants carry composite tenant ownership. Add directory-role assignments for supported probe rules. Limit all submitted strings/collections and reject embedded NUL.
- [x] **Step 4: Populate the minimum operation-driven permission catalog.** Graph User.Read delegated; Organization.Read.All and supported Directory.Read.All alternatives in each applicable kind; additional permissions only for the pinned connection contract. Exchange and ARM permissions remain distinct. Unsupported grants fail explicitly.
- [x] **Step 5: Verify migration from 002, fresh install, credential revocation and permission administration through real HTTPS/CLI.** Re-run M1 export tests to prove no secret leakage.
- [x] **Step 6: Commit:** feat: persist local identities credentials and grants.

## Task 3: Discovery, signing keys and app-only OAuth

**Files:** Create identity/keys.py, tokens.py, oauth/errors.py, forms.py, discovery.py, routes.py, tests/unit/test_identity_tokens.py, tests/integration/test_identity_app_only.py; extend bootstrap/config and add migration 004_signing_keys.py and administration rotation command.

**Interfaces:**
- SigningKeyStore(session, wrapping_key): active_key(now), public_jwks(now), rotate(now).
- issue_access_token(context: PrincipalContext, now: datetime, keys: SigningKeyStore) -> str.
- validate_access_token(raw: str, service: str, now: datetime, session: AsyncSession) -> PrincipalContext.
- OAuthError(error: str, description: str, status: int = 400).
- POST /{tenant}/oauth2/v2.0/token accepts client_credentials and a single resource/.default; JSON response token_type=Bearer, expires_in, access_token; never refresh_token for this flow.

- [x] **Step 1: Write tests for local discovery/JWKS, secret and certificate clients, rotated keys and signed token validation.** Assert `response.status_code == 200`, `"refresh_token" not in response.json()`, roles contain only granted application permissions, and forged/expired/wrong-audience/ID tokens receive 401 at validation.
- [x] **Step 2: Add table-driven invalid-assertion tests:** bad signature, unknown key, wrong iss/sub/aud, absent jti, future nbf, expired or excessive validity, duplicate jti on two workers, none/HS256 algorithms. Assert only one concurrent replay insert succeeds.
- [x] **Step 3: Run targeted tests to confirm failures; implement keys, bounded forms and token endpoint.** Cap form bodies at 64 KiB and reject repeated security-relevant fields. Use library JOSE primitives with algorithm allowlists, not hand-written signature encoding. Return invalid_client/invalid_scope/unsupported_grant_type according to the supported contract.
- [x] **Step 4: Implement explicit wrapping-key initialization/rotation administration and readiness checks.** Existing instances get an explicit identity initialization step; ordinary startup must not create or replace missing material.
- [x] **Step 5: Run tests, then authenticate a pinned MSAL Python confidential client against local metadata.** Explicitly disable external instance discovery or provide verified local metadata; assert an actual request succeeds and wrong secret fails.
- [x] **Step 6: Commit:** feat: add persistent signing identity and app-only OAuth.

## Task 4: Permission-enforced Graph probes and early Exchange feasibility

**Files:** Create authorization/evaluator.py, providers/graph/probes.py, errors.py, tests/integration/test_identity_graph.py, integrations/python/app_only.py and integrations/powershell/Test-AppOnlyConnection.ps1. Add only contract-enumerated Exchange bootstrap files/routes from Task 1.

**Interfaces:**
- OperationRequirement(service, application_permissions, delegated_permissions, user_roles, access_rule).
- authorize(context: PrincipalContext, operation: OperationRequirement, session: AsyncSession) -> None; raises a typed denial, not a boolean callers can ignore.
- GET /v1.0/organization and /beta/organization: Graph collection envelope from the authenticated tenant.
- GET /v1.0/me and /beta/me: delegated signed-in local user's id/displayName/userPrincipalName/mail; no application-token /me.
- Organization fields: id, displayName, verifiedDomains; additional implemented fields only from persisted tenant state. User.Read delegated gets the documented basic organization projection. Broader reads enforce the documented operation's applicable permission/role requirements.
- $select only for these fixed fields; other query options fail explicitly. No general OData engine in M2.

- [ ] **Step 1: Write an authorization matrix test.** App Organization.Read.All succeeds; absent application permission fails 403; delegated User.Read reads own /me/basic organization only; application token on /me fails; Exchange/ARM-audience tokens fail 401. Changing tenant identifiers never exposes another tenant.
- [ ] **Step 2: Run failures, implement requirements and provider-specific responses.** Keep management authentication independent and preserve M1 Host/error regressions.
- [ ] **Step 3: Run real Python Graph SDK and PowerShell Get-MgOrganization with locally issued tokens and configured service roots.** Verify response IDs/fields, not merely connection success.
- [ ] **Step 4: Attempt the real pinned ExchangeOnlineManagement bootstrap immediately.** Supply a local resource token and supported endpoint switches from Task 1; capture sanitized HTTP method/path/status/request sequence. Implement only documented/source-established bootstrap contracts and authorization needed for a real connection and one harmless discovery/probe command identified in that contract. No mailbox evidence implementation yet.
- [ ] **Step 5: Resolve or report transport restrictions.** Endpoint/auth adapters may establish context but cannot replace service cmdlets or suppress permission failures. If the module insists on unconfigurable upstream transport or unsupported bootstrap, publish concrete evidence and request a design adjustment before substituting a transport. Do not mark this gate passed.
- [ ] **Step 6: Verify focused tests and actual supported probes; commit:** feat: enforce provider permissions and qualify app-only connections.

## Task 5: Delegated authorization code, PKCE and consent

**Files:** Create oauth/authorization.py, pages.py, sessions.py, migration 005_oauth_state.py, tests/unit/test_identity_redirects.py, tests/integration/test_identity_authorization.py; extend OAuth routes and identity administration.

**Interfaces:**
- AuthorizationRequest(client_id, tenant, redirect_uri, scopes, state, nonce, code_challenge, code_challenge_method).
- begin_authorization(request: AuthorizationRequest, session: AsyncSession, now: datetime) -> UUID.
- complete_authorization(transaction_id: UUID, user_id: UUID, consent: bool, mfa_outcome: str, session: AsyncSession, now: datetime) -> AuthorizationResult(callback_uri: str, code: str | None, state: str, error: str | None). This server-side function accepts an authenticated local user, never an unverified browser-supplied user ID.
- redeem_code(code: str, client_id: UUID, redirect_uri: str, verifier: str, session: AsyncSession, now: datetime) -> TokenResponse.
- TokenResponse: access_token, token_type, expires_in, scope; optional id_token and refresh_token according to granted OIDC/offline_access scopes.
- GET authorize creates a persisted transaction; local GET/POST /login, /consent and /mfa forms operate on its opaque handle. POST token with grant_type=authorization_code validates client authentication for confidential clients.
- Identity administration can assign supported directory roles and revoke sessions; these operations require the management credential.

- [ ] **Step 1: Test the browser transaction end to end through HTTP.** Assert state is returned unchanged to the validated callback, PKCE S256 verifier binds redemption, ID token nonce matches, consented delegated scopes use scp rather than roles, and no refresh token appears without offline_access.
- [ ] **Step 2: Test rejection paths.** Invalid/external callback stays on the local server; missing/wrong CSRF fails; HTML in displayName is escaped; denied consent returns access_denied to a previously validated callback; ordinary user cannot grant admin-required scopes. Disabled users/clients and changed redirect registrations fail. A second code redemption on another API instance returns invalid_grant.
- [ ] **Step 3: Run targeted tests and observe failures.** Implement state transitions and atomic single-use redemption, then add minimal rendered forms using escaped values and explicit form actions. Codes are hashed in storage; transaction state cannot be chosen or altered by the browser.
- [ ] **Step 4: Implement persistent user and administrator consent.** Restrict approvals to permissions configured for the client and resource. Support preconsent through management and browser consent by a qualified local administrator. Admin-consent route/parameters are included only where the pinned SDK requires them; no grant-all shortcut.
- [ ] **Step 5: Run an MSAL Python public-client authorization-code/PKCE exchange.** The browser or scripted HTTP test must actually authenticate and consent locally; do not fabricate a code. Test explicit simulated MFA success/denial and record its limited meaning.
- [ ] **Step 6: Commit:** feat: add delegated PKCE sign-in and persistent consent.

## Task 6: Device authorization, refresh and session revocation

**Files:** Create oauth/device.py, migration 006_device_outcomes.py and tests/integration/test_identity_sessions.py; extend sessions.py, routes.py, pages.py and session administration. Migration 006 adds device transactions and authentication outcomes without editing already-committed migrations.

**Interfaces:**
- start_device_authorization(tenant_id, client_id, scopes, session, now) -> DeviceAuthorization.
- approve_device_code(user_code, user_id, consent, session, now) -> None.
- redeem_device_code(device_code, client_id, session, now) -> TokenResponse.
- refresh_session(refresh_token, client_id, requested_scopes, session, now) -> TokenResponse.
- revoke_session(tenant_id: UUID, session_id: UUID, session: AsyncSession, now: datetime) -> None.
- DeviceAuthorization fields: device_code, user_code, verification_uri, expires_in=900, interval=5, message. Browser GET/POST /device uses the same login/consent checks as Task 5.
- POST token supports urn:ietf:params:oauth:grant-type:device_code and refresh_token; device code is bound to issuing client and tenant.
- Management GET /_mock/tenants/{tenant_id}/identity/sessions lists metadata only; DELETE /sessions/{id} revokes the session and all refresh handles.

- [ ] **Step 1: Test pending, slow_down, approval, denial and expiry using an injected clock.** Fast polling increases the stored interval by five seconds; assert authorization_pending before approval, slow_down on early repeat, authorization_declined on denial, expired_token after the deadline and invalid_grant after successful redemption. Both replicas must see the same state.
- [ ] **Step 2: Test refresh and revocation.** Concurrent valid refreshes succeed consistently; wrong client/tenant fails; absolute expiry never extends; removing consent/grants prevents minting removed scopes; requesting extra unconsented scopes fails. Revocation blocks every handle without pretending to erase existing access-token claims.
- [ ] **Step 3: Run failures; implement locking/compare-and-set transitions, hashed handles and bounded polling.** Retain token digests until their required replay/expiry windows end. Cleanup is bounded, tenant-safe and never deletes live session/key data.
- [ ] **Step 4: Persist minimal authentication outcome records.** Store tenant/client/user IDs where known, flow, timestamp, correlation ID, result and simulated-MFA result; omit raw secrets/codes/tokens and unbounded untrusted descriptions. These support later Graph sign-in projection; do not advertise Graph auditLog endpoints in M2.
- [ ] **Step 5: Verify the real MSAL device-flow poller and silent token acquisition/refresh against local HTTP.** Restart an API between approval and redemption; confirm results and revoked-session behavior survive.
- [ ] **Step 6: Commit:** feat: persist device authorization and revocable delegated sessions.

## Task 7: Real SDK and collector connection qualification

**Files:** Create integrations/powershell/Connect-MockAzure.ps1, Test-DelegatedConnection.ps1, Test-CollectorConnections.ps1, integrations/python/delegated.py, docs/compatibility.md; finish integrations/contracts, manifest and compose.clients.yaml. Extend providers only for Task 1's documented connection probes.

**Interfaces:**
- Connect-MockAzure -ConfigPath <local-json> -Mode Application|Device|AuthorizationCode creates real SDK contexts using the pinned local profile and user-provided local credentials.
- ClientConfig schema_version=1: endpoints, issuer, tenant_id, client_id, resource audience aliases, CA path and authentication mode; contains no secret.
- CLI mockazure client-config --tenant ID --client ID --profile host|container --output PATH writes this format.
- Probe result schema_version=1: client/version/revision, flow, request-contract ID, expected object IDs, actual IDs, allowed/denied outcome, isolation result and capability limitations. No bearer values or raw credential-bearing captures.

- [ ] **Step 1: Test configuration generation.** Assert every transport endpoint is configured locally, no real-tenant default exists, profile choice changes reachable endpoints without changing token identity, and exports contain no secret.
- [ ] **Step 2: Run failures; implement commands and launchers.** Use Graph custom environments and explicit local authority metadata. If a client requires endpoint/auth adaptation, retain its original collection code, version the adapter and document the precise changed boundary.
- [ ] **Step 3: Complete app-only and delegated SDK matrix.** Python MSAL/Graph and Graph PowerShell must make actual authorized and denied requests. Test expiration, wrong audience and removed grants at the appropriate issuance/resource boundary. Pin every imported module, not just top-level packages.
- [ ] **Step 4: Exercise Hawk and MES connection wrappers.** Graph must establish a context and return the correct organization; Exchange must complete actual module command loading and its Task 4 probe. For MES, configure and exercise Az.Accounts plus the exact ARM connection discovery requests identified in Task 1. Support only the needed ARM discovery routes, persisted tenant/subscription fields and api-versions; do not fabricate subscription inventory or advertise Activity Logs.
- [ ] **Step 5: Prove no fallback.** Remove a required permission and supply an invalid local credential; each client must fail locally. Capture permitted destination records; verify external DNS and direct connection attempts cannot leave the isolated network. Python-only socket monkeypatching is insufficient evidence for PowerShell.
- [ ] **Step 6: Record the exact compatibility matrix and commit:** test: qualify local SDK and collector authentication paths.

Configuration-only use is preferred. Passing an emulator-issued token through a documented SDK parameter is allowed, but the matrix must distinguish it from native SDK acquisition through a custom authority. Do not claim the latter based on the former.

## Task 8: Restart, distribution and independent review

**Files:** Create tools/verify_identity.py, tests/acceptance/test_identity_distribution.py, docs/identity.md, docs/identity-execution.md; update Docker/native initialization, README and capabilities.

**Interfaces:**
- verify_identity.py --config PATH --client-manifest integrations/manifest.json --report work/identity-report.json orchestrates live tests against a dedicated M2 instance.
- Report includes source commit, package hash, dependency/image versions, UTC timestamp, probe outcomes, restart/rotation results and isolation evidence; it excludes credentials and raw tokens.
- Capabilities list each supported auth flow, resource operation, permission and explicit limitation, not simply "Microsoft authentication supported."

- [ ] **Step 1: Add acceptance assertions for required report fields and successful qualification outcomes.** Missing/stale manifest inputs, skipped client probes or a failed isolation check fail the gate; no existence-only report check.
- [ ] **Step 2: Verify two API replicas against one PostgreSQL instance.** Acquire tokens on one and validate on the other; concurrently redeem a code, rotate keys, refresh and revoke sessions. Restart API/worker/DB containers and verify credentials, grants, sessions and key identity remain intact.
- [ ] **Step 3: Test distribution parity.** Build/install a clean wheel, initialize native and Docker instances, migrate an M1-only DB explicitly, run both auth modes and provider probes. Verify CA and wrapping-key volume paths survive recreation. Native HTTPS and CLI work without changing machine trust.
- [ ] **Step 4: Run required verification once the implementation settles.**
  - .venv/Scripts/python.exe -m pytest tests/unit tests/integration -q
  - .venv/Scripts/ruff.exe check src tests tools integrations
  - .venv/Scripts/python.exe tools/verify_identity.py --config .runtime/m2/native.json --client-manifest integrations/manifest.json --report work/identity-report.json
  - .venv/Scripts/python.exe -m pytest tests/acceptance/test_identity_distribution.py -q
  - Re-run the existing foundation live verifier and acceptance checks using their documented dedicated instance arguments; preserve running M1 services.
  - Build wheel/sdist and run tools/check_package.py using its existing documented interface.
  All required suites must pass; unavailable PostgreSQL/Docker/client probes are reported as incomplete, not skipped success.
- [ ] **Step 5: Update capabilities, operational backup/restore guidance and compatibility documentation.** Document key/secret/session lifecycle, real-flow versus token-injection support, exact unsupported policy/auth features, observed SDK limitations and later milestone boundaries.
- [ ] **Step 6: Commit the verified increment, run one independent whole-change review under Native execution, resolve findings and rerun affected verification.** Record decisions and results in docs/identity-execution.md, then push and attach the implementation PR. Preserve the M1 dependency explicitly if its PR is still open.

## Deliberate limits and feasibility decisions

- No password grant, on-behalf-of, workload identity federation, B2C, external federation, real MFA provider or personal Microsoft accounts.
- No unbounded multitenant consent. All accepted principals/service principals belong to locally provisioned tenants; public apps can connect to other local tenants only through explicit provisioning and consent.
- Conditional-access policy enforcement, Graph mutation/evidence projections, OData/delta and extensive resource coverage remain M3.
- Exchange mailbox commands and UAL workflows remain M4; ARM resource actions and Activity Log collection remain M5.
- The connection contract discovered in Task 1 is a prerequisite artifact because an actual pinned module may need undocumented bootstrap details. Those details must be enumerated and reviewed against the accepted adapter boundary before implementation. A need for additional product scope triggers design review; it is not blanket authorization for arbitrary provider expansion.
- Dependency versions are deliberately resolved and pinned during the acquisition task, matching M1's process. Qualification may not proceed until the lock/manifest contains exact versions, immutable revisions and hashes.

## Primary references

These define protocol expectations; a client's behavior is evidence of compatibility requirements, not authority for changing Microsoft semantics.

- [OAuth client credentials](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)
- [Certificate assertions](https://learn.microsoft.com/en-us/entra/identity-platform/certificate-credentials)
- [Authorization code and PKCE](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [Device authorization](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code)
- [Graph organization permissions](https://learn.microsoft.com/en-us/graph/api/organization-get?view=graph-rest-1.0)
- [Graph custom environments](https://learn.microsoft.com/en-us/powershell/module/microsoft.graph.authentication/add-mgenvironment?view=graph-powershell-1.0)
- [Exchange connection parameters](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/connect-exchangeonline?view=exchange-ps)

## Plan self-review

Coverage: authentication and persistent key/session requirements map to Tasks 2, 3, 5 and 6; operation permissions and protocol errors to Tasks 3 and 4; SDK/collector connections and containment to Tasks 1, 4 and 7; restart/packaging/review to Task 8. The five Review Focus conditions have explicit tests. Later Graph/Exchange/ARM/scale milestones remain separate.

The unresolved Exchange/Az bootstrap behavior is an explicit early feasibility gate, not an assumed success or placeholder endpoint. No additional user product decision is needed before testing it within the approved adapter constraints. Report a discovered incompatibility before changing those constraints.
