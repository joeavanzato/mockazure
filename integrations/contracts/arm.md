# M5 ARM client and collector qualification

All identities, credentials, subscriptions, CA material and evidence belong to
the isolated M5 runtime. No real tenant, public API fallback or host trust change
is permitted. Container clients use an internal network and a deliberately unusable
external DNS resolver. Native Python clients and wheel-installed servers load a
loopback-only socket audit guard. Requests transports additionally validate the
configured origin at request and prepared-send boundaries, including redirects.

## Original clients

- MSAL1.39.0 authenticates confidential applications and public-client delegated
  browser flows against local HTTPS. Negative principals have no ARM assignments.
- Python azure-mgmt-resource23.4.0, azure-mgmt-authorization4.0.0 and
  azure-mgmt-monitor6.0.2 use unchanged generated SDK operations. Their public
  transport/base URL/version options are configured explicitly.
- Az.Accounts4.0.2 and Az.Resources7.8.0 are original manifest/hash-verified modules.
  Their custom environment supplies local ARM/login/Graph endpoint URLs. Token
  login uses fresh tokens issued by MockAzure. Original Get-AzAccessToken,
  Get-AzSubscription, group/tag/RBAC commands and Invoke-AzRestMethod run unchanged.
- ObjectType User is an original New-AzRoleAssignment parameter, used for the known
  synthetic target. No command returns substituted or canned objects.

Nine checks in application and delegated modes cover discovery,103 resources,
group lifecycle/deletion, tags, built-in definitions, assignments,1003-row Activity
paging, exact filter/select behavior and denied Activity access. Three profiles:
native Python against clean-wheel API replicas, container Python and container
PowerShell. Python datetime models have microsecond precision; exact wire values
are independently checked using the SDK's public raw-response hook.

The API-version contracts actually emitted by Az are pinned separately:
resource groups2021-04-01; tags2024-07-01; role definitions2022-05-01-preview;
role assignments2020-08-01-preview. Stable Python RBAC uses2022-04-01.
Activity Logs uses2015-04-01. See docs/arm-contracts.md for bounded wire compatibility.

## Original MES

Microsoft-Extractor-Suite revision47565f7842784cd3667ff630e3dccb49fa3408ed is
verified against its public archive manifest. Original declarations, types,
logging initializer and Get-AzureActivityLogs.ps1 are loaded unchanged.
Only the existing permitted HTTP-origin remapping boundary wraps Invoke-RestMethod;
it preserves headers, methods and PowerShell response conversion and rejects
redirects or other origins. Unconfigured reconnects fail.

Eight cases cover application/delegated, discovered/explicit subscriptions and
allowed/denied identities. Allowed cases export the deterministic1003 historical
records and native mutation evidence in separate windows. Historical expected
IDs/payloads/timestamps are generated independently of production projection.
The runtime first verifies known group create/delete facts; after all client
mutations publish, it captures the native API snapshot for collector output
comparison. Both kinds of JSON outputs are checked field-for-field, including
exact100ns instants, unique event IDs and correct scope/action. Files are never
repaired, merged or reshaped by the oracle.

Denied cases demonstrate a403 preflight and no exported records. Original
Get-AzSubscription can terminate explicit lookup with its observed
PSArgumentException saying the inaccessible subscription was not found.
Unrelated exceptions do not count as successful denial.

## Runtime and report gates

Seventeen required runtime checks cover clean-wheel installation/package bytes,
two API replicas and workers, API/PostgreSQL/worker restart, pending deletion,
saved Activity pages, tenant/scope isolation, page reauthorization, native evidence,
429/503 retry and DNS/IPv4/IPv6 network boundaries.

tools/verify_arm.py binds its report to source, README/licence/notice, dependency
lock, client/archive manifest, actual ARM schema references, built wheel, runtime
and client image IDs, collector sources and every saved output artifact.
Incomplete or failed verification writes a non-success report. Acceptance requires
literal success for every runtime/client/case gate and fresh matching artifacts.

M4's four original collector output failures remain combined-release blockers.
Passing M5 neither waives those failures nor permits package publication or merging.
