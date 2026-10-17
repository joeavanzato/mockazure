# MockAzure M4: Exchange and Unified Audit Log

Status: adopted for Native execution under the user's explicit overnight
delegation. M3C is qualified (522 tests,20 runtime gates,three SDK profiles,
20 original collector cases) and published as PR7. The author self-reviewed this
design against primary references and original collector closures; the user did
not separately review this document. No routine approval pause is required.

## Intent and selected architecture

Build operational local APIs for mailbox configuration/forwarding, inbox and
sweep rules, message trace, and Graph Unified Audit Log queries. Original Hawk
and Microsoft Extractor Suite functions are acceptance clients, not substitutes
for API contracts. Preserve canonical tenant/user/application/permission state,
real local authentication, PostgreSQL transactions, and durable jobs.

The selected approach extends the existing database and evidence/job engines.
A separate Exchange fixture database would diverge from identity and permission
state. Arbitrary PowerShell execution would undermine bounded supported
operations. Neither alternative meets the intended API fidelity.

No SMTP delivery, internet mail forwarding, IMAP/EWS server, Defender, broad
Purview compliance, or transport-rule engine is implied. Forwarding and rules
are operational configuration with transactional audit effects. A local message
input path may simulate supported rule effects for tests, but does not send mail
or fabricate a full mail transport service.

## Global constraints

- All work remains under D:/projects/mockazure. Never access or modify VTW.
- PostgreSQL18, Python>=3.12, existing FastAPI/SQLAlchemy/Alembic architecture.
- Actual Graph v1.0 CSDL roots and REST operations; beta aliases only for declared
  compatible profiles. Pin source revisions/hashes and retain source licences.
- Real Microsoft packages and original collector functions remain unchanged.
- No real tenants, host trust/DNS/firewall changes, or cloud fallback.
- Fresh own M4 runtime/worktree/test resources; preserve all predecessor runtimes.
- Native implementation, one final independent reviewer, frequent commits,
  pushed feature PR for user merge. Continue M5/M6 afterward.
- No newly waived required collector output; inherited M3B MES audit JSON
  limitation is not a waiver for M4 outputs.
- Exact measurements, not estimated compatibility or throughput claims.

## Persisted state and ownership

Mailbox identity is tenant plus canonical user ID. ExchangeGuid is a distinct
persistent UUID; ExternalDirectoryObjectId is the actual user ID. Resolve user
object ID/UPN and mailbox GUID/SMTP/alias only inside that tenant. Ambiguous
names fail explicitly. User rename is reflected in identity fields; tombstoned
users cannot acquire fresh mailbox access. Disabled-user mailboxes remain
administratively inspectable where the real operation permits it.

Store explicit mailbox properties: display/name identity projection, primary
SMTP and aliases, mailbox type, forwarding address/internal recipient reference,
forwarding SMTP address, DeliverToMailboxAndForward, archive configuration,
CAS protocol flags, change timestamp, and folder/statistics input state.
Administrative provisioning/import validates typed values. Never invent an
active mailbox for every existing directory user.

InboxRule holds tenant, mailbox ID, opaque stable rule ID, unique priority/order,
Graph-compatible actions/conditions/exceptions, enabled/read-only/hidden flags,
and Exchange-only properties where explicitly supported. SweepRule is a
distinct persisted rule family. Do not expose hidden Exchange rules as ordinary
Graph inbox rules. Reject mutation of read-only rules; changing sequence updates
the ordering transactionally.

Mailbox/forwarding/rule changes publish immutable operation facts in the same
transaction. Audit projection derives UAL records from those captured facts;
later object rename/deletion cannot rewrite evidence. Imported history carries
explicit provenance and never masquerades as a newly executed operation.

## API operations

Graph:
- GET/POST /users/{id|UPN}/mailFolders/inbox/messageRules and /me equivalent.
- GET/PATCH/DELETE /users/{id|UPN}/mailFolders/inbox/messageRules/{ruleId}.
- POST/GET /security/auditLog/queries.
- GET /security/auditLog/queries/{id} and /{id}/records.
- Return documented201 create,200 rule update/entity reads,204 delete;
  validate exact v1.0 properties and enum values.

Exchange command transport uses the real ExchangeOnlineManagement3.11.0 module.
Extend the already approved bounded bootstrap with fixed server-generated
proxies for explicitly supported commands. Each proxy makes a real local HTTP
InvokeCommand call; arguments remain JSON data, never executable source.
Unsupported commands/parameters fail explicitly. No hand-written replacement
module or collector functions. Do not intercept compiled EXO cmdlets with
lookalike client functions; implement their actual requested REST routes.

Required command families:
- Get-Mailbox, Get-EXOMailbox; Set-Mailbox forwarding/configuration subset.
- Get-InboxRule including IncludeHidden; New/Set/Remove/Enable/Disable-InboxRule
  for the declared predicates/actions.
- Get-SweepRule and bounded sweep lifecycle via documented commands.
- Get-EXOMailboxStatistics, Get-EXOMailboxFolderStatistics, Get-EXOCasMailbox
  with archive and selected property behavior from persisted state.
- Get-MessageTraceV2 and Get-MessageTraceDetailV2.

Implement any additional concrete route in the selected original collector or
real client closure before claiming that workflow passes. Do not substitute
empty outputs to bypass missing operations.

## Authorization and entitlements

Graph rule reads use MailboxSettings.Read or ReadWrite as documented; writes
require MailboxSettings.ReadWrite. Delegated calls are scoped to the signed-in
mailbox unless an operation-specific documented sharing model is implemented;
application calls require application permission and tenant isolation. /me
requires a delegated token. Resolve mailbox ownership before returning data.

Exchange permission is two-stage: a valid Exchange audience/scope or application
role, then the actual supported command/recipient scope. Recheck current roles
and enabled identities at invocation, not just bootstrap. Global Reader is not
sufficient for Get-InboxRule according to its official command documentation.
Do not reuse the current Get-OrganizationConfig reader-role policy for all commands.
Represent mailbox-scoped assignments explicitly and prevent cross-mailbox access.
Exact supported role mappings and licence requirements must be recorded from
primary references in Task1; do not invent Entra roles for Purview or Exchange
management roles.

UAL requires AuditLogsQuery.Read.All or the appropriate service-specific
AuditLogsQuery-* permission, in the correct application/delegated kind.
AuditLog.Read.All alone is not equivalent. Workload-scoped access restricts both
query metadata and returned records; all-service permission does not bypass
tenant boundaries. Existing token grant claims remain immutable per M3C;
current principal/role/entitlement state and query access are rechecked.

The v1.0 single-query GET documentation currently lists ThreatIntelligence.Read.All,
conflicting with create/list/records and the published UAL permission family.
Resolve against pinned permission metadata and the coherent audit-query operation
contract, document the discrepancy, and do not accidentally grant UAL access
from a threat-intelligence permission.

## Query and continuation semantics

Inbox rules support declared select/top and stable local continuation only where
the operation permits it. Unsupported filters/options return an explicit error.
No successful no-op parameter handling.

MessageTraceV2 follows its own contract: last90days, default48hours,
maximum10days per request, default1000/max5000 results, UTC output, and
continuation via the last Received/RecipientAddress pair using EndDate and
StartingRecipientAddress. Persist indexed trace/recipient rows and detail events.
Tie ordering must be deterministic and must not drop records at equal timestamps;
document and test the supported duplicate-recipient tie representation.
Implement the documented request-rate behavior or an explicitly configurable
local equivalent, including429 and Retry-After.

UAL POST validates a time window and all declared service/operation/record type/
UPN/IP/object/administrative-unit/keyword filters, then enqueues a typed durable job.
Use SQL filtering and SQL materialization into immutable query items; no loading
the tenant dataset into a Python list. The job captures a publication watermark
and occurrence window, uses the existing lease/fence protocol, survives crashes,
and cannot publish stale work twice. Retention must preserve rows needed by a
pending query until materialization completes.

Query status is server-owned (notStarted/running/succeeded/failed as defined by
pinned schema). Submitted read-only status never drives execution. The original
MES client submits empty status; accept an ignored read-only input only as a
documented compatibility boundary and always return a valid schema enum.
The create documentation's serviceFilter singular normalizes to the resource
schema's serviceFilters collection; conflicting simultaneous forms fail400.

Query records are immutable after success, paged with opaque tenant/query/caller
bound handles, reauthorized on each page, with an explicit expiry and410 behavior.
Query results survive source retention until their own expiration. Clients can
poll existing IDs after an API/worker/PostgreSQL restart. Unknown, failed,
pending, expired, and forbidden queries never masquerade as successful empty
results. Exact pending/failed record errors must be documented and tested.

## Acceptance clients and required outputs

Pin and hash the existing original source revisions:
Hawk a44536f2b4225dd8ea2e463db88d5db40cfac8c7;
MES47565f7842784cd3667ff630e3dccb49fa3408ed.

Required original workflows:
1. Hawk Get-HawkUserEmailForwarding: exact forwarding recipients/flags and both
   ordinary and investigation CSV/JSON files.
2. Hawk Get-HawkUserInboxRule: visible forwarding/deletion rules, investigation
   output and real sweep-rule output. Hidden rules also tested through the real
   Exchange command separately.
3. Hawk Get-HawkUserConfiguration: mailbox, archive/statistics, folders and CAS
   outputs from actual persisted values; include its full helper closure.
4. MES Get-MailboxRules and Get-MailboxRulesGraph: actual IDs/actions/conditions.
5. MES Get-MessageTraceLog: actual trace rows across the5000-result boundary.
6. MES Get-UALGraph -UseV1: create/poll/download all expected records and native
   change evidence, plus supported output formats with exact record identities.

Qualify application and delegated modes where each actual module supports them,
positive and denied cases; negative licences/roles, cross-tenant IDs and
cross-mailbox attempts are mandatory. Denial checks inspect every artifact,
not just status or CSV. Original helpers/sleeps remain unchanged except the
previously accepted telemetry-only suppression and local transport remapping.
A new upstream defect remains visible and must not quietly lower the gate.

Real Graph SDK Python and PowerShell exercise rule CRUD and UAL lifecycle.
Real EXO module exercises supported commands over actual HTTP. Run clean-wheel
native Python and isolated container clients. Bind reports to source/lock/
manifest/wheel/images/original collector/output hashes. Test API replicas,
worker races, worker/API/PostgreSQL restarts, throttling/retries and stable pages
under state mutation. M6 owns the100k-user/1m-evidence scale release profile.

## Primary references inspected during design

- https://learn.microsoft.com/en-us/graph/api/mailfolder-list-messagerules?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/mailfolder-post-messagerules?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/messagerule-update?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/resources/security-auditlogquery?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/security-auditcoreroot-post-auditlogqueries?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/security-auditcoreroot-list-auditlogqueries?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/security-auditlogquery-get?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/graph/api/security-auditlogquery-list-records?view=graph-rest-1.0
- https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-inboxrule?view=exchange-ps
- https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-sweeprule?view=exchange-ps
- https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-mailbox?view=exchange-ps
- https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-messagetracev2?view=exchange-ps
- https://learn.microsoft.com/en-us/exchange/permissions-exo/role-assignment-policies

Task1 must pin exact primary documents/metadata and resolve remaining operation
details before implementing each contract. No real-tenant experiment is authorized.
