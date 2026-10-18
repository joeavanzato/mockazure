# Client compatibility

All qualified clients use explicit local authorities/endpoints and the generated CA.
There is no upstream service fallback. SDK/module/collector pins are in
../integrations/manifest.json and uv.lock. Transport adapters and patched collectors
are named explicitly; they are not represented as unmodified end-to-end compatibility.

## Qualified baseline

The last combined release qualification is implementation commit 8c7287c.
[Its results](stabilization-results.md) and [machine-readable gates](stabilization-qualification.json)
include all five client suites and the full million-record scale run. Later source
changes require their own checks; historical pass reports do not transfer automatically.

| Workflow | Proven coverage / boundary |
| --- | --- |
| MSAL / Azure Identity | Local app-only and delegated flows; native/container managed identity profiles |
| Graph Python / PowerShell | Selected directory, evidence, security, mail and audit workflows; explicit local context |
| ExchangeOnlineManagement 3.11.0 | Actual module download and local HTTP command proxies with persisted API/RBAC checks |
| Az PowerShell / Azure SDKs | Inventory, supported mutations/RBAC and Activity Logs |
| Hawk / MES evidence | Selected sign-in/audit/security workflows, authorized and denied cases |
| Mail collectors | 44 passing cases under the explicit serialization-v1 source patch profile |
| MES Azure collection | Eight original Activity Log collector cases |

PowerShell qualification uses locally acquired tokens; native credential acquisition
is not claimed for every module. Complete collect-everything entry points remain
outside this baseline. [Collector patch details](../integrations/collector-patches/README.md).

## Directory coverage expansion qualification

The 2026-10-17 directory qualification ran `python -m tools.verify_directory_coverage`
on a freshly initialized Docker lab with original msgraph-sdk 1.64.0 and Microsoft
Graph PowerShell 2.35.1. Both resolved four mixed directory objects, traversed three
group-membership pages (page size one), and deserialized organization contact fields
and built-in role permissions. Initialization, HTTPS identity readiness, persistent
restart and missing-storage guards also passed. This qualifies those exact operations;
it does not certify every Graph command or refresh the historical whole-product gates.

## Collection protocol qualification

The 2026-10-17 `python -m tools.verify_collection_protocol` run passed on a fresh
local Docker lab using original Microsoft Graph PowerShell 2.35.1. It retrieved
two applications through page-size-one traversal and an injected continuation 429,
checked selected credential fields without secret disclosure, checked count,
read a bounded 20-member expansion and all 23 members through the separate paged
relationship collection, and verified five batch outcomes (three allowed, one
forbidden, one invalid query). Raw batch requests use the existing local transport
binding for the pinned module's authentication provider; Microsoft command code is
unchanged. The preceding original Python/PowerShell directory checks also passed.
This qualifies those request combinations, not every SDK command or query option.

## Exchange collection qualification

The 2026-10-17 `python -m tools.verify_exchange_collections` fresh Docker run passed
with original ExchangeOnlineManagement 3.11.0 for all six added configuration and
permission commands. Original MES `Get-TransportRules` exported the exact expected
CSV row, including timestamp; both loaded collector source hashes matched the pinned
archive. The module uses the existing bounded service-generated proxies and a local
TCP relay for its port-443 bootstrap, with no host trust or DNS changes. Persistence,
HTTPS readiness and missing-storage checks passed. Complete Hawk tenant configuration
and MES mailbox-permission workflows remain outside this qualification because their
additional commands/fields are still unsupported.

## Mail content qualification

The 2026-10-17 `python -m tools.verify_mail_content` run passed against the final
mail implementation in a freshly initialized local Docker lab. Original msgraph-sdk
1.64.0 and Microsoft Graph PowerShell2.35.1 each retrieved two folders and all101
messages at page size25, verified exact MIME and file-attachment SHA-256 hashes,
and resumed saved folder delta links after PostgreSQL and API restart. Generated
property updates produced the expected changed message, and a local move produced
the expected source-folder removal. Both module-tree checks passed before and after
execution. All340 runtime source hashes matched the checkout.

The PowerShell generated delta command was checked separately from saved-link
resumption through the original Invoke-MgGraphRequest, using the existing local
transport/authentication binding. No Microsoft command implementation was replaced.
This qualifies the listed requests, not arbitrary mail queries, shared-mailbox
permissions, sending or complete tenant collection. The dedicated CI job repeats
this bounded101-message workflow; the256MiB storage test remains opt-in.

## Cross-workload coverage qualification

The 2026-10-18 `python -m tools.verify_coverage_program` fresh Docker run passed
all ten stages: directory, Exchange, mail content, SharePoint/OneDrive, Teams,
Intune, Activity feed, authentication-method management, authentication policies,
and two-tenant collection recovery. The original Graph SDK 1.64.0 and PowerShell
2.35.1 executed the supported generated operations; Exchange used the previously
approved bounded bootstrap. Archive and extracted-tree hashes were checked before
and after the run. The combined tenant retained earlier workloads while later
stages queried and mutated their own records.

The policy stage read the policy root and typed configurations, changed SMS and
Authenticator targets through the original clients, exercised supported Conditional
Access role/platform/compliance fields, completed actual local HTTPS registered-method
MFA, and verified that disabling that method blocks refresh. Fresh tenants enforce
method eligibility; upgraded tenants retain their explicit legacy profile until the
first successful method-policy write. Local browser adapters select an offered method,
and historical qualification fixtures now explicitly enroll a mobile method. These
adapters simulate user interaction; Microsoft client modules remain unchanged.

The combined recovery step used 404 primary metadata records and a 2 MiB object,
eight readers, two API replicas, issued-cursor retries and restart. The full-size
100,000-record/256 MiB profile is a separate gate; see
[reproduction and measurement boundaries](coverage-program.md). The secondary tenant
exercises mailbox/drive isolation; the integration suite supplies other workload
isolation cases. This is bounded API compatibility, not complete Hawk/MES tenant
collection or every operation offered by those Microsoft modules.

## Preserved upstream limitations

Original Hawk JSON append creates concatenated documents for multiple suspicious rules.
Original MES trace CSV loses fractional timestamp precision. Four original failures and
four original denial passes are retained separately from the successful patched run.

M3B's original MES audit JSON append/merge defect remains the previously accepted,
explicit limitation. Exact retrieval is independently checked and SOF-ELK output passes.
No additional output defect is silently accepted.

## Current operation contracts

- [Graph directory](../integrations/contracts/graph-directory.md)
- [Graph evidence](../integrations/contracts/graph-evidence.md)
- [Graph security](../integrations/contracts/graph-security.md)
- [Mail and audit](../integrations/contracts/mail.md)
- [Azure management](../integrations/contracts/arm.md)

[Historical reports and decisions](archive/m1-m6/README.md) retain the original milestone
outcomes and failed attempts. See [default runtime](default-runtime.md) for supported
startup/storage behavior.
