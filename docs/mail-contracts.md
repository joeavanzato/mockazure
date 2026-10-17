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

## Message trace profile

Get-MessageTraceV2 uses the pinned official command reference:90-day lookback,
default48hours, at most10days per range, default1000/max5000 rows. UTC Received
is ordered descending, then normalized RecipientAddress ascending. Subsequent
requests use the last row's Received as EndDate and its RecipientAddress as
StartingRecipientAddress. Server selection and limits execute in PostgreSQL;
there is no Python tenant-wide materialization.

The two-field Microsoft continuation cannot distinguish different messages
with the identical Received+RecipientAddress pair. This profile requires that
pair to be unique within an imported tenant and rejects collisions with409,
atomically, without rewriting forensic timestamps. Equal timestamps across
different recipients and repeated recipients at different timestamps are
supported, including microsecond precision. Importers must supply sufficiently
precise source timestamps for repeated recipients. This is a bounded input
contract, not a claim about undocumented Microsoft tie behavior.

Authenticated management POST /_mock/tenants/{id}/mail/traces imports immutable
recipient rows and detail events with explicit source provenance. Identical
reimports are idempotent; changing an existing identity's facts fails409.
The source retains original normalized facts. Trace mailboxes are optional
explicit scope associations; scoped readers see only associated permitted
mailboxes, while administrators/unscoped command grants can read tenant data.

Sender/recipient, MessageId/traceID, status, source/destination IP, subject mode,
and detail Event/Action filters execute on persisted state. Trace detail requires
both traceID and recipient. Unknown or out-of-scope traceIDs return no rows,
and an unauthorized command fails403. The default detail window also uses48hours.

Budgets are tenant-and-command-wide rolling windows:100 accepted query requests
per300seconds, serialized in PostgreSQL and committed independently of provider
transactions. Excess requests return429 with integer Retry-After until the
oldest accepted request expires. Replicas share the same budget. Query parameters
with explicit dates use ISO8601 UTC on the wire; fixed PowerShell proxies convert
their typed DateTime values to UTC before JSON serialization.

## Native and historical UAL records

Mail configuration and inbox/Sweep mutations enqueue their immutable source in
the same PostgreSQL transaction. A rollback publishes neither state nor intent.
No-op writes create no mail fact; source revisions, job fences and projection
receipts provide replay deduplication. Actor and target names come from captured
facts, so later renames cannot rewrite audit history. Current source recovery
also finds legacy mail facts that predate eager enqueueing.

The shared evidence family is auditLogRecord; its wire type is the actual
Graph v1.0 microsoft.graph.security.auditLogRecord. Existing generation,
publication-watermark, export pin and retention mechanisms apply. UAL actors
can be users, service principals or system strings, so this family does not
misinterpret its userId field as a directory-user subject foreign key.

Native mail mutations project ExchangeAdmin records with Common audit fields,
Exchange Admin ExternalAccess/TokenObjectId/TokenTenantId and immutable modified
properties. Old/new values are strings as used by the Common audit representation;
absence is the empty string and compound values are JSON text. The Graph wrapper
uses the exact pinned CSDL enum spelling. MailboxOwnerUPN/Id retain the operation's
mailbox snapshot. No client IP was captured by the original intent format, so
clientIp/ClientIP are explicitly null; no address is invented.

Common/Exchange Admin reference is pinned to MicrosoftDocs/office-365-management-api
revision f22300eab36df54f1efb07be8b23e229d1ff0537, source SHA256
94968478423c3af5624b34c062631377f90eec90871f6a5a50b17d9275381e19:
https://learn.microsoft.com/en-us/office/office-365-management-api/office-365-management-activity-api-schema

Historical UAL import/export preserves public payloads, timestamps and provenance.
OrganizationId must match the owning tenant, and createdDateTime must agree with
the source occurrence time. Dynamic audit JSON remains bounded by the CSDL
profile. Credential/auth-header fields, including credential name/value pairs,
are rejected rather than retained in this public evidence profile.

Indexed UAL service and operation dimensions are bounded to128 and256 characters
respectively, preventing valid-looking imports from failing later on PostgreSQL
index tuple limits. Tenant UUID comparison is case-insensitive while exported
source payload text remains unchanged.

## Durable Graph audit searches

POST/GET /v1.0/security/auditLog/queries and query GET/records use the pinned
security.auditLogQuery and security.auditLogRecord contracts. The beta alias
exposes this same bounded v1.0 profile. The request status is ignored; persisted
job state controls notStarted/running/succeeded/failed. Query IDs/count fields are
read-only. serviceFilter is accepted as the documented singular alias; conflicting
plural input fails. Record-type enum input accepts the documented lower-initial
spelling and emits the pinned CSDL spelling.

All query filters combine with AND; entries within each collection combine with OR.
SQL predicates cover time, workload, operation, record type, UPN, client IP, object
and administrative unit. Keyword is a case-insensitive literal substring over the
workload auditData dictionary after removing Common-schema fields, with SQL wildcard
characters escaped. It is not a full text search-language implementation. Text is
bounded to1024 characters and filter lists to100 values. Missing times use the
captured evidence clock's last24hours. Historical visibility is additionally
bounded by the tenant's configured evidence retention and publication watermark.

Audit Standard/Premium is checked on every operation. Delegated callers additionally
need a distinct authenticated-management Purview Audit Logs/View-Only Audit Logs
grant at /_mock/tenants/{tenant}/mail/audit-roles/{user}/{role}; Entra roles do not
substitute. Workload permissions map Exchange, Entra, OneDrive, SharePoint, CRM,
Endpoint to Exchange, AzureActiveDirectory, OneDrive, SharePoint, CRM, Endpoint
respectively. Unknown workloads require AuditLogsQuery.Read.All. A scoped caller
omitting service filters gets its authorized workload union. Query metadata/results
require access to every requested workload; listing excludes inaccessible queries.

Ruling: reuse the shared EvidenceCursor retention pin under EvidenceState locking
rather than create a second cleanup protocol. Each pending query pins its captured
generation/publication/time range; SQL INSERT SELECT materializes immutable results
under the existing job epoch/lease fence. Post-insert lease loss rolls back the
entire transaction. Successful results survive source cleanup and evidence reset.
Failures remain explicit; no partially populated successful query.

Local result lifetime is7days; page handles last1hour and cannot outlive the query.
Expired query/page access is410. Cleanup of expired result rows is an M6 hardening
task; expiry already denies reads. Server pages contain at most100 records, accepting
$top1..1000 and $select. Unknown options fail. Continuations bind tenant, caller,
permissions, API version and query, and replay the same ordered record page.
List pages capture a creation-time bound but report current job statuses. Live
database restart and original-client qualification remain separate runtime gates.

Original MES submits serviceFilter as an empty string when unspecified. This
normalizes to no filter; nonempty conflicting singular/plural values still fail.

## Original collector compatibility details (Task9)

The pinned Exchange 3.11.0 public Mailbox CLR model exposes ArchiveDatabase as
System.String. MockAzure returns a stable virtual database name derived from the
persisted archive GUID, or null when no archive exists. This supports the
unchanged Hawk archive-discovery branch; it does not simulate Exchange database
placement. Reflection evidence is recorded in integrations/contracts/exchange-mail.json.

The bounded generated InboxRule proxies retain structured Address/Name recipient
objects and attach a fixed ToString method returning Address. This preserves
recipient facts for original MES comma-joined CSV output. No collector or
Microsoft module function is replaced. The public historical Exchange model
documents ForwardTo as ADRecipientOrAddress[]:
https://learn.microsoft.com/en-us/previous-versions/office/developer/exchange-server-2010/ff322625(v=exchg.140).
Exact Exchange display-name formatting remains outside this bounded projection.

Collector qualification uses separate original module scopes, connection cleanup,
and output directories for each invocation in one PowerShell batch. It includes
original module state initialization as well as function/type declarations. The only
suppressed operations remain telemetry and module-level update/UI entry points.
Both Graph and Exchange service calls go to the isolated local runtime.

The Hawk inbox fixture has three visible rules, with one rule combining suspicious
forwarding/deletion actions, plus a persisted sweep rule and a hidden rule tested
separately through Get-InboxRule -IncludeHidden. Hawk's original investigation
serializer appends one JSON document per suspicious rule; multiple suspicious
rules in a single invocation would produce invalid JSON. The qualification does
not claim that upstream output combination. Every output produced by the
qualified invocation must parse and match canonical state; malformed JSON is
never accepted, repaired, or silently skipped. Fixtures are restored afterward.
