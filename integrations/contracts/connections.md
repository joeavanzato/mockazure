# Pinned connection contracts

This is source-derived input to live qualification, not a compatibility claim.
Exact artifact hashes and dependencies are in ../manifest.json; Python transitive
packages and hashes are in uv.lock, dependency group clients. Third-party source
and modules are acquired under ignored work/vendor and are not redistributed.

## Python and Graph PowerShell

MSAL Python 1.39.0 uses local authority OIDC metadata. Disable central instance
discovery explicitly; client-credentials requests send scope=resource/.default.
The certificate and delegated flows must be tested through their real MSAL APIs.
Microsoft Graph Python SDK 1.64.0 uses an explicit request-adapter base_url and
allowed local hosts; its credential must acquire a local token.

Graph PowerShell Authentication and Identity.DirectoryManagement are both
2.35.1. Add-MgEnvironment configures AzureADEndpoint and GraphEndpoint.
Connect-MgGraph accepts an explicit environment and app/delegated credentials or
a SecureString access token. Test acquisition separately from access-token
injection. Get-MgOrganization sends GET /v1.0/organization.

## Hawk

Commit a44536f2b4225dd8ea2e463db88d5db40cfac8c7.
Hawk/internal/functions/Test-GraphConnection.ps1 calls Get-MgOrganization; when
unauthenticated it calls Connect-MgGraph without endpoint parameters.
Hawk/internal/functions/Test-EXOConnection.ps1 calls Get-OrganizationConfig and
falls back to Connect-ExchangeOnline when that command is unavailable.
Pre-establish a correctly scoped local context, invoke these original functions,
and enforce network isolation even on their error paths. Dot-sourcing the selected
original connection functions is a narrow connection gate, not qualification of
the full Hawk module or its unrelated dependencies. Out-LogFile diagnostics may
be supplied by the harness; never replace Graph/Exchange service commands.

## Microsoft Extractor Suite

Commit 47565f7842784cd3667ff630e3dccb49fa3408ed.
Scripts/Connect.ps1 defines Connect-M365 and Connect-AzureAZ. They forward bound
parameters to Connect-ExchangeOnline / Connect-AzAccount after versionCheck.
The former exposes ConnectionUri, AzureADAuthorizationEndpointUri, AccessToken,
Organization, AppId, CommandName and certificate/device switches.
The latter exposes Environment, AccessToken, AccountId, Tenant, Subscription,
ServicePrincipal, GraphAccessToken and MicrosoftGraphAccessToken.
Qualify these original functions. Treat versionCheck's update lookup as a
preparation-only check: any adapter disabling that network check must document
the exact boundary and immutable source, without changing collection logic.
MES Graph collection uses its Graph context; it has no Graph connection wrapper
in this pinned Connect.ps1.

## ExchangeOnlineManagement 3.11.0

Dependencies: PowerShellGet 2.2.5 and PackageManagement 1.4.8.1. Runtime:
PowerShell 7.5.0 on the manifest-pinned Linux image.
The shipped netCore/ExchangeOnlineManagement.psm1 passes explicit endpoint,
organization, credential/access-token and command-name arguments to
Get-ConnectionContext. It then calls New-EXOModule, which downloads/generates the
command module. A successful Get-ConnectionInformation alone does not prove that
Get-OrganizationConfig exists or works.

Early live feasibility must capture the exact New-EXOModule requests before
provider implementation. Verify the complete command bootstrap and actual
Get-OrganizationConfig invocation with role checks. Do not provide a hand-written
replacement for the downloaded service command. Keep banner/help/telemetry off.
The C# assemblies contain /adminapi transport and command metadata schemas;
string inspection alone does not establish a complete request/response contract.
Any unresolved module transport limitation is a milestone gate.

## Az.Accounts 4.0.2

No RequiredModules dependencies; .NET libraries are bundled in the pinned archive.
Add-AzEnvironment provides explicit local authentication and ResourceManager URLs.
Connect-AzAccount supports explicit resource tokens. Disable context autosave and
telemetry within the isolated process. Capture required tenant/subscription
discovery paths and api-versions in the live contract before implementing those
operations. A SkipValidation/SkipContextPopulation-only connection is not evidence
that resource discovery works.

## Isolation and reproducibility

Run preparation with external artifact access, then launch compose.clients.yaml.
Both clients have only an internal network, no inherited proxy, and a failing
external resolver. Tests check external DNS, direct IPv4 and direct IPv6 sockets.
Generated state lives in project-owned .runtime/clients; no host trust/DNS changes.
The default 10.231.72.0/24 was verified unused on the development host; set
MOCKAZURE_CLIENT_SUBNET to an unused private subnet on hosts where it overlaps.
The runtime image's /root and /tmp paths are project bind mounts.

Existing source inspection does not yet prove custom authority compatibility,
Exchange bootstrap, ARM discovery, or either collector connection gate.


Assembly inspection additionally established GET {NewEXOModuleBaseUri}/{tenant}/EXOModuleFile?Version=3.11.0, with bearer authorization and CommandName/SkipLoadingFormatData headers. The successful response is a value array containing fileName, fileContent and exportedCmdlets. The client writes and imports the returned .psm1, then binds TokenProviderObjectId and ConnectionContextObjectId. Task 4 must determine the configured base URI and verify this sequence live. This is downloaded executable command transport, not a JSON resource-list response. Inspection used project-local ILSpy 9.1.0.7988; derived third-party source remains ignored under work/.
