# M4 mail and audit operation contracts

These are implementation contracts, not a claim that M4 is qualified yet.

## Pinned structural and operation sources

Graph CSDL revision34b8e9c29032c82352a6fd7138be0a864a842bb2 supplies
messageRule, messageRuleActions, messageRulePredicates, and security.auditLogQuery,
auditLogRecord, auditData/defaultAuditData and their dependent types.
The existing metadata manifests retain the full source hashes. Permission IDs
come from Graph docs revision98a98cc88051a857c119a2e1bb61587633585284.
The canonical permission catalog now includes both kinds of MailboxSettings.Read,
MailboxSettings.ReadWrite, and all seven AuditLogsQuery permission families.

mail/data/references.json pins35 public operation files by repository, revision,
path and SHA256, including Graph v1.0 resource/operation pages and18 Exchange
commands at c5386f4fae7364d13ae8759abb6bbe1fc1be0dde. Public downloaded copies
remain ignored research material; generated manifests contain no tenant data.

## Graph rules

[Resource](https://learn.microsoft.com/en-us/graph/api/resources/messagerule?view=graph-rest-1.0),
[create](https://learn.microsoft.com/en-us/graph/api/mailfolder-post-messagerules?view=graph-rest-1.0),
[update](https://learn.microsoft.com/en-us/graph/api/messagerule-update?view=graph-rest-1.0).

Reads accept MailboxSettings.Read or ReadWrite; mutations require ReadWrite,
with application/delegated kinds kept distinct. Delegated consent is not
admin-required in the official permission catalog; application consent is.
Delegated mailbox ownership and application tenant boundaries are additional
checks, not permission-name substitutes. Create returns201, PATCH200, DELETE204.
Create requires actions/displayName/sequence; the server owns id/hasError/isReadOnly.

Ruling: reject caller writes to isReadOnly. Although an operation table includes
it, its resource semantics describe a server-controlled inability to mutate or
delete a rule. Allowing a caller to clear it would defeat that restriction.
Cost if wrong: an unsupported writable flag, rather than a protection bypass.

Ruling: resource CSDL requires id, while create cannot accept a server-owned id.
Validate create/update fields structurally as partial resource payloads, then
apply explicit operation requirements. Response validation still requires id.

## UAL

[Create](https://learn.microsoft.com/en-us/graph/api/security-auditcoreroot-post-auditlogqueries?view=graph-rest-1.0),
[list](https://learn.microsoft.com/en-us/graph/api/security-auditcoreroot-list-auditlogqueries?view=graph-rest-1.0),
[records](https://learn.microsoft.com/en-us/graph/api/security-auditlogquery-list-records?view=graph-rest-1.0).

AuditLogsQuery.Read.All permits all supported workloads. The CRM, Endpoint, Entra,
Exchange, OneDrive and SharePoint variants permit only their corresponding
workloads. The operation permission intersection is only the first gate; query
creation, metadata and record paging must enforce workload scope. AuditLog.Read.All
and ThreatIntelligence.Read.All confer no UAL access.

Ruling: the single-query GET page's ThreatIntelligence permission contradicts
the related operations and permission catalog. Use the coherent AuditLogsQuery
family throughout. Cost: fidelity to an apparent documentation defect is not preserved.
Ruling: accept documented singular serviceFilter by normalizing it to CSDL
serviceFilters; reject conflicting forms. Ignore server-owned status on create
(including original MES status:""); always return a valid server status.
CSDL record-type members use PascalCase while REST prose uses lower camel case;
preserve pinned CSDL exactly and normalize documented input aliases in the query
operation, never globally loosen enum validation. Cost: explicit alias maintenance.

[auditData](https://learn.microsoft.com/en-us/graph/api/resources/security-auditdata?view=graph-rest-1.0)
has a dynamic dictionary. Only this dictionary and defaultAuditData admit
bounded dynamic JSON; ordinary Graph objects remain closed. Bounds are16 nesting
levels,10,000 collection/dictionary members,16KiB strings,256-character keys,
finite numbers/signed64-bit integers and a1MiB mail-operation envelope. Dynamic
annotations are rejected. Alias resolution uses each schema's own context;
one schema's self alias cannot capture another schema's types.

## Exchange authorization profile

[App-only authentication](https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2?view=exchange-ps),
[feature permissions](https://learn.microsoft.com/en-us/exchange/permissions-exo/feature-permissions),
[command permissions](https://learn.microsoft.com/en-us/powershell/exchange/find-exchange-cmdlet-permissions?view=exchange-ps).

Require Exchange.ManageAsApp for application tokens or Exchange.Manage for
delegated tokens, then current operational permission and recipient scope.
Global Administrator/Exchange Administrator provide the supported administrative
operations; Global Reader is restricted to configuration reads, never inbox/sweep
rules or mutations. Explicit local Exchange assignments hold allowed command names
and optional mailbox scope, representing custom Exchange RBAC command grants.
Do not claim these are a complete Microsoft built-in role/parameter catalog.
No Entra role string is silently treated as an Exchange custom role.

[Get-InboxRule](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-inboxrule?view=exchange-ps)
explicitly excludes Global Reader/View-Only Organization Management.
Ruling: no implicit end-user MyBaseOptions or mailbox-owner Exchange admin access;
require a configured command grant or supported administrator role. Exact tenant
RBAC entries require online discovery, which is forbidden here. Cost: unconfigured
real-world end-user grants are outside this bounded profile. Graph self-mailbox
permission remains independently supported.

## Command and parameter boundaries

The pinned18 command documents are authoritative for parameter names, types,
cloud applicability and response semantics. Supported implementation subsets:

- Get-Mailbox/Get-EXOMailbox: identity/UPN/SMTP/object-ID selectors as actually
  offered, result size, selected properties/property sets, archive and supported
  recipient type. Set-Mailbox: forwarding fields and delivery-to-both setting.
- Inbox lifecycle: mailbox/rule identity, name, enabled/priority, supported
  conditions/actions/exceptions; IncludeHidden and ResultSize on reads.
  Sweep lifecycle: sender/source/destination folder, name, enabled,
  KeepForDays/KeepLatest, ExceptIfFlagged/ExceptIfPinned.
- EXO mailbox statistics/folder statistics/CAS: identity selectors, archive and
  the documented projection/folder options required by original Hawk.
- TraceV2/detail: documented date/address/message-ID/status/IP/subject/event
  filters and their exact parameter types. No silent parameter no-ops.

Unsupported switches (including scope-bypass options) fail explicitly. Additional
parameters encountered in the selected unchanged collectors must be implemented
before acceptance, not silently ignored. Common PowerShell presentation/control
parameters are handled by the actual module.

[Set-Mailbox](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-mailbox?view=exchange-ps)
gives internal ForwardingAddress precedence over ForwardingSmtpAddress.
This configures local state and evidence; no external delivery occurs.
[TraceV2](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-messagetracev2?view=exchange-ps)
uses UTC, a90-day lookback, default48-hour window, maximum10-day request,
default1000/max5000 results, and EndDate/StartingRecipientAddress continuation.
The local configurable rate limiter models100 requests/5minutes with Retry-After.

## Entitlements and audit roles

[Audit overview](https://learn.microsoft.com/en-us/purview/audit-solutions-overview)
supports Audit Search Graph API in Standard as well as Premium; do not impose an
E5-only search gate. [Audit setup](https://learn.microsoft.com/en-us/purview/audit-get-started)
distinguishes organization subscription, per-user premium features and Audit Logs/
View-Only Audit Logs roles. These are Purview roles, not fabricated Entra roles.

Ruling: persist explicit local Exchange service and Audit Standard/Premium
entitlements rather than infer them from Entra P1/P2 or invent a complete commercial
SKU matrix. Query authorization checks current entitlement; delegated UAL additionally
requires a local Purview Audit Logs/View-Only Audit Logs grant. Application UAL uses
the documented Graph application permission. Existing retained evidence is not
rewritten when subscription state changes. Cost: SKU-to-entitlement provisioning
is an explicit emulator control until its broader mapping is implemented.
Ordinary mailbox provisioning requires Exchange entitlement; explicitly provisioned
shared mailboxes do not require a made-up personal E5 licence. Archive state and
statistics must be real persisted input, never synthesized success for absent archives.


## Canonical mailbox identity and provisioning

M4 also pins the v1.0 user-update operation: cloud-managed UPN updates are
normalized, restricted to the tenant's verified domain, and uniqueness checked
under the existing serialized directory-write transaction. Mailbox GUID/SMTP
remain stable after a UPN change, while current UPN/displayName are projected
from the canonical user. Historical mail facts retain their captured identity.
This extends the predecessor's intentionally narrower PATCH profile; its negative
test now rejects an unverified-domain UPN instead of rejecting all UPN changes.

The authenticated mail management PUT replaces declared mailbox provisioning
state and folder inputs. Omitted CAS flags use the explicit local default enabled
except SmtpClientAuthenticationDisabled=false. Empty folder input represents
known zero imported content, not an invented nonempty mailbox. Existing forwarding
settings persist across provisioning updates. Archive creation allocates a distinct
GUID; absent archives fail statistics lookup.

Mail configuration mutations append native facts to the shared immutable evidence
source table in the same transaction. Task6 will add UAL projection, enqueueing and
pending-source recovery before M4 qualification; Task2 alone does not claim UAL output.


## Inbox rule ordering and paging

Mutation serializes on the mailbox row. Priorities are normalized to consecutive
one-based positions, with insertion positions clamped to the current rule list.
The unique priority constraint is deferred until commit so concurrent reorder
cannot leave duplicates. Opaque IDs remain stable. Hidden rules do not appear
through Graph, and directly modifying/deleting read-only rules fails.

Local bounds:1000 rules per mailbox, maximum100 items per page, accepted top1..1000.
Select is supported; filter/expand/orderby are explicitly rejected for this profile.
The nextLink preserves the original query and mailbox rule revision for one hour;
later update/deletion cannot rewrite that page. It is bound to tenant, generation,
client, subject, permission claims, mailbox and API version. Current identity,
entitlement and mailbox access checks still apply at every page. Version retention
is currently conservative; lifecycle cleanup is an M6 hardening concern.

## Original Exchange compiled transport

The pinned ExchangeOnlineManagement 3.11.0 archive remains byte-for-byte original.
Its compiled Get-EXOMailbox, Get-EXOMailboxStatistics,
Get-EXOMailboxFolderStatistics and Get-EXOCasMailbox use these observed local
OData routes, with base64 UTF-8 identity keys and isEncoded=true:

- Mailbox('key') and CasMailbox('key') return typed entities.
- Mailbox('key')/Exchange.GetMailboxStatistics() returns a typed collection;
  Archive=true selects the separate persisted archive GUID/folders.
- Mailbox('key')/MailboxFolder/Exchange.GetMailboxFolderStatistics(
  folderscope=Exchange.ElcFolderType'All') returns typed folder rows.

The public module's generated model property types are recorded in
integrations/contracts/exchange-mail.json with its archive hash. Statistics
sizes on the wire are Int64 bytes; the unchanged cmdlet formats those values.
Unsupported fields/options/scopes fail explicitly. There is no mail delivery
engine: folder counts/sizes are explicit imported state, and Sweep rules are
persisted configuration.

The compiled module's OData reader needs the original Microsoft
Extensions.DependencyInjection.Abstractions 8.0.0 assembly, absent from the
pinned package/container combination. The qualification harness loads the
separately pinned Microsoft NuGet package before module import. This is a
client dependency, not a command replacement. Public provenance:
https://www.nuget.org/packages/Microsoft.Extensions.DependencyInjection.Abstractions/8.0.0
No Microsoft module file is modified.

Default Connect-ExchangeOnline sends CommandName=*. MockAzure exports only
implemented commands authorized by current role/command grants; explicit
unsupported names fail. Fixed generated proxies are limited to the declared
configuration/rule command table, use JSON data, preserve null forwarding
values, and perform real local HTTP calls. Compiled EXO commands remain in
Microsoft's assembly. Enable/Disable-InboxRule retain their own permissions
and immutable audit operation names.

Current accepted transport profile has exact-value checks for primary/archive
statistics, folders, CAS, canonical mailbox identity, and forwarding/inbox-rule
writes through the original module. Full original collector qualification is
still Task9; no collector compatibility claim is made by this task alone.
