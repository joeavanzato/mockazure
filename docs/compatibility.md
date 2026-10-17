# Identity and connection compatibility

Pinned acquisition: integrations/manifest.json and uv.lock. Runtime qualification
uses compose.clients.yaml plus compose.clients.m2.yaml on an internal-only network.
External DNS and direct IPv4/IPv6 checks pass for Python and PowerShell after the
M2 API, database and ingress are present. No upstream service fallback exists.

| Client | Demonstrated path | Boundary |
| --- | --- | --- |
| MSAL Python 1.39.0 | Native client credentials, authorization code/S256, device flow and silent refresh | Explicit local authority (including common), instance discovery disabled, local CA |
| Graph Python SDK 1.64.0 | Organization and signed-in user reads; permission denial | Local base URL and allowed host; standard SDK middleware retained |
| Graph PowerShell 2.35.1 | Get-MgOrganization with application, authorization-code and device tokens; permission denial | Explicit local token plus custom environment; native secret acquisition failed in isolation |
| Invoke-MgGraphRequest 2.35.1 | Local /me with delegated tokens | Explicit Authorization header required for the custom host |
| ExchangeOnlineManagement 3.11.0 | Connect-ExchangeOnline, actual command-module download, Get-OrganizationConfig; ungranted app rejected; API consent plus operational role required | Explicit local Exchange token, local port 443; approved server-generated HTTP proxy |
| Az.Accounts 4.0.2 | Connect-AzAccount and Get-AzSubscription with provisioned inventory | Explicit local ARM token/custom environment; Reader assignment controls visibility |
| Hawk a44536f2b4225dd8ea2e463db88d5db40cfac8c7 | Original Test-GraphConnection and Test-EXOConnection | Pre-established local SDK contexts; selected functions only |
| MES 47565f7842784cd3667ff630e3dccb49fa3408ed | Original Connect-M365 and Connect-AzureAZ | Endpoint/token parameters forwarded unchanged; versionCheck update lookup moved to preparation |

PowerShell 7.5.0 and all imported modules are pinned in the manifest. This table
does not qualify full collection workflows, Hawk's default reconnect fallback,
Exchange native credential acquisition, or Azure native authority acquisition.

The Exchange proxy contains a fixed Get-OrganizationConfig function which uses
the Microsoft module's current connection context to make a real local HTTP
request. It returns PostgreSQL tenant state after audience, tenant, API permission
and persisted operational-role checks. It does not embed tokens or return a canned organization. Additional
Exchange commands are explicitly rejected. See identity-exchange-bootstrap-decision.md.

Graph Python's /me request requires the standard URL-replacement middleware.
Passing a bare custom HTTPX client omits that middleware and sends an internal
SDK placeholder path; the qualification harness now preserves the standard chain.

Use mockazure client-config --tenant UUID --client UUID --profile host|container
--output FILE --config CONFIG to export a non-secret connection profile.
Configure client_profiles.host and client_profiles.container explicitly; both
retain service_endpoints.login as the canonical issuer. Only configured authorities
select profiles. Host and forwarding headers never become generated destinations.

Connect-MockAzure.ps1 establishes a real Graph context. Application mode obtains a
local client-credentials token. Device/AuthorizationCode mode accepts a protected
token file produced by an actual local MSAL flow; it does not claim native Graph
PowerShell acquisition. The qualification Python script automates local lab user
forms using its dedicated test-user file; ordinary clients can use the browser UI.

Reports and lab credentials remain ignored under .runtime/clients. Do not share
token files. The API, CA, host trust stores and production DNS remain separate.

The final review added an isolated same-process profile-switch regression.
Connect-MockAzure rejects differing login/Graph endpoints before credential
access; switching instances requires a fresh PowerShell process. Final
qualification and acceptance verify the complete executed module/collector trees
against their archives, including unexpected files and sibling module versions.

## M3A directory qualification

| Client/profile | Exercised behavior |
| --- | --- |
| MSAL 1.39.0 + Graph Python 1.64.0, native and container | App-only and authorization-code delegated create/read/page/update/member/delta/delete, missing-permission denial |
| Graph PowerShell Users/Groups/Authentication 2.35.1, container | Same lifecycle in both modes, generated group delta bootstrap and opaque delta-link traversal |
| Clean wheel native API, two replicas | 5,000-user snapshot under mutation, continuation replay, API/PG restart, delta resume, tenant/audience rejection, shared one-shot 429/retry |
| Isolated clients | DNS/IPv4/IPv6 external connection probes fail; native Python audit guard limits API/client processes to loopback |

The Python SDK emits matching @odata.type annotations and delta() routes; both
are accepted. Mismatched type annotations fail. PowerShell's generated delta
cmdlet returns objects without exposing a delta-link parameter, so continuation
rounds use Microsoft's Invoke-MgGraphRequest. These remain real local HTTP requests,
not replacement cmdlets. PowerShell receives explicit locally acquired tokens;
its native credential acquisition is not claimed.

Run tools/verify_graph.py only against the dedicated M3 stack. Its versioned report
binds results to the current source fingerprint, wheel, lockfile, pinned client
archives/trees and running image. Full collection gates remain M3B.


## M3B evidence qualification boundary

The final combined qualification passed all 25 runtime gates, three native/container SDK
profiles in both authorization modes, and all 20 collector acceptance gates (14 strict passes; 6
explicitly accepted original MES audit JSON limitations). SDK/runtime checks took 581.622
seconds; the complete run took 2842.423 seconds. The report is bound to source, wheel, lockfile,
manifest, original collector files, outputs and the running image.

Native and container Azure Identity managed identity acquisition passed. Hawk JSON/CSV, MES
sign-in JSON/SOF-ELK, audit SOF-ELK, empty results, permission denials and licence denials pass
their original output checks. MES audit JSON retains the documented upstream append/merge defect
with exact retrieval independently checked. See [verification
summary](evidence-verification.json).

Graph PowerShell uses locally acquired access tokens. The MES-only transport
adapter preserves original requests and Microsoft's raw request command,
authentication and retry handlers. It binds the existing credential provider to
the configured local hostname and disables redirects; unfamiliar pinned handler
layouts fail closed. The original service calls and serializers are unchanged.
See evidence-execution.md for pinned revisions, output gates and limitations.
