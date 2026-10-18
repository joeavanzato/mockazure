# Coverage expansion: proposed design and delivery program

Status: **proposed for review; no runtime implementation authorized by this document**.
Scope decisions confirmed on 2026-10-17. Baseline: main `96625a3`, including the
[COVERAGE.md inventory](../../../COVERAGE.md). Native execution remains selected.

## Outcome

Make MockAzure useful for complete local evidence collection workflows: enumerate a
resource, follow its relationships, retrieve evidence and content, continue after a
restart, and observe correct permission and policy failures. Persist data and changes
in PostgreSQL. Scenarios are optional test fixtures, not the public product model.

The user selected the entire four-phase program. Phase 3 prioritizes SharePoint,
OneDrive, Teams, Intune and the Office 365 Management Activity feed. Phase 4 includes
both authentication-method policies and improvements to Conditional Access, alongside
authentication-method management. The tables below define a bounded first profile
for each workload; they do not promise the whole Microsoft API.

## Constraints

- Python 3.12+, existing FastAPI/SQLAlchemy/psycopg stack, PostgreSQL 18, Windows and Docker.
- Work only in MockAzure; never modify VTW or copy its secrets, generated files or untracked files.
- No real-tenant traffic; use official public Microsoft documentation and pinned schema sources.
- Native execution; commit cohesive changes, push reviewable PRs, do not auto-merge.
- Graph additions target v1.0 contracts; do not infer beta support from a shared router.
- No host trust, DNS or firewall changes; all generated service/content URLs stay local.
- Current COVERAGE.md describes shipped behavior; planned features remain separately labeled.
- Preserve existing data and accepted collector limitations; no silent destructive migration.

## Architecture decisions

Keep identity, authorization, typed query compilation, PostgreSQL migrations, durable
jobs and existing evidence publication. Add workload modules with explicit schemas,
queries and authorization. Avoid routing all new objects through the security-alert
JSON store or rewriting already qualified providers.

Three alternatives were considered: widen the generic evidence store for everything;
create a separate service/database for each workload; or keep one service/database
with workload modules and small shared primitives. Choose the third. It preserves
transactional consistency and simple local operation while keeping Graph-specific
semantics outside generic storage.

### Metadata, binary content and changes

Mail owns mailbox/folder/message metadata. A new `workloads` package owns versioned
SharePoint/OneDrive, Teams and Intune resource metadata and relationships. Revisions
are scoped by tenant, workload and collection scope; indexes cover those keys plus
public ID and supported query dimensions. Opaque public IDs are preserved, including
composite site IDs, Teams IDs and mail IDs; do not impose the directory UUID grammar.

A small `content` package stores immutable binary manifests and 1 MiB PostgreSQL bytea
chunks. Proposed local default maximum is 256 MiB per object, configurable; this is
an emulator limit, not a Microsoft service limit. Stage then finalize uploads after
size/hash verification. Metadata references only finalized content. Stream reads and
single byte ranges without materializing an entire large object. Deduplicate only
within a tenant. Mail MIME, attachments, drive versions and Teams hosted content reuse
this store. Do not fetch imported attachment URLs or arbitrary remote content.

Imports are explicit local admin operations: typed metadata upserts/tombstones, staged
binary content, and atomic metadata batches (default 1,000 records/8 MiB). They return
revision and accepted count. Reject dangling/cross-tenant references atomically.
Fixture imports do not fabricate successful user actions or their audit records.
Public mutations that are implemented publish native facts transactionally.

Stable collection snapshots and delta checkpoints persist in PostgreSQL. Bind handles
to tenant, principal, resource, query and authorization scope; reauthorize every page.
Changed access must never expose a previously accessible page. Retention/expiry errors
must be explicit and documented. Delta is implemented only where Microsoft defines it:
mail-folder messages and drive-root items in this program. Teams has no invented delta.

Every new store participates in tenant reset, export/import, maintenance and upgrade
checks as part of its owning PR. Reference-aware pruning preserves content needed by
live versions/cursors. Export archives include a typed manifest and binary hashes;
restore validates tenant identity/references before committing. Tokens and passwords
are excluded from ordinary evidence export.

### Authorization and operational behavior

Use existing current-identity checks and service-specific audiences. Permissions are
operation-specific and distinguish delegated scopes, application roles, directory
roles, entitlements and resource ACLs. A stored permission object is evidence; it must
not silently become OAuth consent. Selected-site grants and Teams resource-specific
consent are explicit authoritative records separate from imported display metadata.

Compile authorized filters before pagination. Define $select/$filter/$orderby/$expand
and caps per operation using official references, not a universal OData promise.
Unsupported operations/options fail visibly. Every added collection has restart-safe
paging, local next links, deterministic injected 429 with Retry-After, and negative
permission tests. Read-only batch support expands only to qualified Graph routes.

Use pinned Microsoft Graph v1.0 metadata for type closure, official REST pages for
operations/permissions, and official Exchange command and Management Activity API
references for those services. Store URL, retrieval date, source hash and version in
an operation manifest. A type in CSDL alone does not establish an operation contract.

## Delivery sequence

Ten cohesive implementation slices, normally one PR per slice. These are dependency
boundaries, not ten separately managed chats. Implement in this chat using Native
execution after design/plan approval. Each PR includes its data lifecycle, tests,
coverage-table changes and qualification evidence. Adjacent slices can share a PR only
if still independently reviewable; never create a setup-only PR.

| Phase / slice | Deliverable | Depends on | Detailed plan |
| --- | --- | --- | --- |
| 1A | Directory lookup, relationships and richer reads | Current main | [Phase 1](../plans/2026-10-17-coverage-phase-1.md) |
| 1B | Query, batch and throttle consistency | 1A | Phase 1 |
| 1C | Exchange evidence/configuration reads | 1A | Phase 1 |
| 2A | Mail content, shared binary storage and message delta | 1B, 1C | [Phase 2](../plans/2026-10-17-coverage-phase-2.md) |
| 3A | SharePoint/OneDrive discovery, content, permissions, delta | 2A | [Phase 3](../plans/2026-10-17-coverage-phase-3.md) |
| 3B | Teams/chat evidence and file relationships | 3A | Phase 3 |
| 3C | Intune inventory, policies and observed compliance | 1B; sequentially after 3B | Phase 3 |
| 3D | Management Activity subscriptions and pull feed | Existing UAL; after 3A-C | Phase 3 |
| 4A | Public authentication-method management | 1A, 1B | [Phase 4](../plans/2026-10-17-coverage-phase-4.md) |
| 4B | Method policies and Conditional Access enforcement | 4A, 3C | Phase 4 |

## Phase 1: make existing collection paths complete

Directory: GET directoryObjects/{id}, POST directoryObjects/getByIds; group memberOf
and transitiveMemberOf; user ownedObjects; device registeredUsers; user/group
appRoleAssignments navigation; delegated /me membership aliases. Group owner $ref and
device registered-user $ref writes maintain shared edges. Existing assignment stores
remain canonical; new navigation does not create a second grant database. Enrich
organization and built-in role-definition responses using supported official fields
and typed admin provisioning. Unsupported fields cannot be invented defaults.

Protocol: implement documented selection/count/order/expansion gaps needed by the
pinned collectors and SDK workflows; retain per-resource allowlists. Extend read-only
batch routing to qualified security and newly added Graph resources. Uniform injection
and shared budgets must not charge an inner batch request twice or leak one caller's
quota across tenants. No batch writes/dependencies in this program.

Exchange: Get-MailboxPermission, Get-RecipientPermission, Get-TransportRule,
Get-TransportConfig, Get-AcceptedDomain and enriched Get-OrganizationConfig. Persist
observed configuration and mailbox permission records; preserve command-specific
roles, grants and mailbox scope. Continue the approved bounded server-proxy approach
with the original module. No transport execution engine or general PowerShell executor.

## Phase 2: mailbox evidence

| Operation family (Graph v1.0) | First profile |
| --- | --- |
| GET /users/{user}/mailFolders and /{folder}; /childFolders | Hierarchy, well-known folder aliases, hidden-folder option, paging; /me aliases |
| GET /users/{user}/messages and /{message}; /mailFolders/{folder}/messages | Typed bodies, headers and metadata, documented query options and body-format preference |
| GET message /attachments and /attachments/{id}; message and attachment /$value | File/item/reference attachment distinctions; raw MIME and supported raw attachment content |
| GET /users/{user}/mailFolders/{folder}/messages/delta | Initial snapshot, changes, tombstones, durable opaque links |
| PATCH message; POST message /move and /copy; DELETE message | isRead/categories updates, folder transfer, deletion and corresponding delta changes |
| GET /users/{user}/mailboxSettings | Typed persisted settings with distinct MailboxSettings permission checks |

Mailbox metadata shares existing Exchange state. Existing imported statistics remain
explicit `statistics-only` data; never turn counts into fabricated messages. A mailbox
can be explicitly migrated to `content-backed` mode after a validated import; then
folder counts/sizes derive from content without mixing legacy totals. Preserve primary
versus archive boundaries; this profile exposes primary Graph mailbox content only.
Message IDs and Prefer: IdType="ImmutableId" have explicit move behavior. No sendMail,
SMTP, draft-composition engine, calendaring, contacts or mail delivery in this program.

Contracts: [message reads](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0),
[folder delta](https://learn.microsoft.com/en-us/graph/api/message-delta?view=graph-rest-1.0),
[MIME](https://learn.microsoft.com/en-us/graph/outlook-get-mime-message).

## Phase 3: workload collection

| Workload | First profile | Explicit boundary |
| --- | --- | --- |
| SharePoint | GET /sites/root, /sites/{id}, /sites/{hostname}:/{path}, /sites?search=; site drives, lists, list items and fields; site permissions list/item/create/delete | Typed stored sites/list items; no SharePoint REST /_api, search service or provisioning engine |
| OneDrive | User/group/site drive discovery; drive/root/item/path/children reads; content, permissions, versions and root delta | Read/import content and mutations via admin fixtures; no public upload sessions, sharing invitations or Office editing |
| Teams | Team metadata, joinedTeams, channels, members; channel messages/replies; chats, chat members/messages; hosted content; filesFolder links to drive items | Explicit private/shared-channel membership and RSC; no calls, meetings, federation, message sending or invented chat delta |
| Intune | managedDevices and user navigation, detectedApps relationships; deviceConfigurations/deviceCompliancePolicies with assignments and device status observations | Licensed tenant required; supported derived types listed individually; no wipe/retire/check-in engine or inferred compliance from assignments |
| Management Activity | POST subscriptions/start and /stop; GET subscriptions/list and /content; GET returned audit content URI | Pull subscriptions, all five content types; webhook delivery and notification-history endpoints deferred |

Graph contracts: [sites](https://learn.microsoft.com/en-us/graph/api/resources/site?view=graph-rest-1.0),
[drive content](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0),
[drive delta](https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0),
[selected permissions](https://learn.microsoft.com/en-us/graph/permissions-selected-overview),
[channel messages](https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-1.0),
[chat messages](https://learn.microsoft.com/en-us/graph/api/chat-list-messages?view=graph-rest-1.0),
[Intune devices](https://learn.microsoft.com/en-us/graph/api/intune-devices-manageddevice-list?view=graph-rest-1.0).

Drive content redirects use short-lived signed local download URLs. Proposed lifetime
is five minutes; already-issued capabilities remain valid until expiry unless the
content is removed. Reauthorize new URL issuance. Do not accidentally forward bearer
credentials to external sites. Imported link metadata never initiates network fetches.

The feed is a separate service at a configurable local `activity` origin, using the
Microsoft Management Activity resource audience, not Graph's audience. Existing endpoint
profiles retain compatible defaults. Subscriptions belong to tenant/client/contentType.
Content is immutable published audit data, not an ad hoc Graph UAL query response.
Types: Audit.AzureActiveDirectory, Audit.Exchange, Audit.SharePoint, Audit.General,
DLP.All. DLP requires its additional permission. Persist publication time independently
of event time, blob IDs, expiration and pagination. Use NextPageUri headers, 24-hour
query windows and seven-day availability; distinguish subscription errors from empty
content. Reject webhook configuration explicitly. [Feed contract](https://learn.microsoft.com/en-us/office/office-365-management-api/office-365-management-activity-api-reference).

## Phase 4: methods and both policy areas

Public management: phone and email create/update/delete; delete Authenticator and
FIDO2 registrations. SMS-primary-sign-in actions remain deferred. Keep canonical
method IDs, per-operation scope/role checks and privileged-target restrictions.
Cloud-only password reset uses POST /users/{user}/authentication/methods/{id}/resetPassword,
returns a durable operation Location, updates the actual verifier and requires password
change on next sign-in. It is delegated-admin only, not app-only or self-service.
Generated passwords are returned once and excluded from logs/exports. Hybrid writeback
and real Authenticator/FIDO2 enrollment/challenges remain outside the local profile.
[Reset contract](https://learn.microsoft.com/en-us/graph/api/authenticationmethod-resetpassword?view=graph-rest-1.0).

Method policies: GET authenticationMethodsPolicy; list/get method configurations;
PATCH supported SMS and Microsoft Authenticator configurations and their documented
target relationships. Enforce enabled state, user/group inclusion and exclusion when
selecting a locally simulated MFA method. Other known configurations may be represented
read-only only with an explicit unsupported-enforcement status; never accept a policy
write whose effect is ignored. Root PATCH registration campaigns and passwordless
configuration remain deferred. Email is not an MFA method. Inventory is not proof of
successful authentication. [Policy contract](https://learn.microsoft.com/en-us/graph/api/resources/authenticationmethodspolicy?view=graph-rest-1.0).

Conditional Access: retain current controls and add directory-role targeting/exclusions,
platform targeting, and compliant-device grant evaluation. Use explicit trusted local
session/device bindings with observed Intune compliance; request headers cannot assert
compliance or completed MFA. Unknown/absent device evidence does not satisfy a compliance
grant. Evaluate AND/OR grant combinations, report-only results, exclusions and current
licenses throughout code/device/refresh flows. Device filter expressions, risk controls,
country geolocation, session controls, hybrid join and authentication strengths remain
explicitly unsupported. No claim of real hardware attestation or push/SMS delivery.
[Condition schema](https://learn.microsoft.com/en-us/graph/api/resources/conditionalaccessconditionset?view=graph-rest-1.0).

Existing tenants retain the explicitly labeled legacy MFA simulation profile on upgrade.
New tenants enforce method policies by default. The first successful method-policy write
atomically enables enforcement for an upgraded tenant; no accepted policy write is ignored.
This transition never fabricates registrations. Expose profile status through MockAzure
admin/capabilities only, not invented Graph response fields.

## Remaining gaps after this program

The program improves all four selected areas; it does not erase every unsupported row.
Defer deleted-object restore, custom roles/PIM, application addKey/removeKey proof flows,
security incident/alert writes, hunting/KQL, eDiscovery/retention, wider ARM providers,
Graph notifications/$metadata, additional OAuth grant types and the exclusions above.
Search-UnifiedAuditLog remains separate from the new feed and Graph UAL. Record each
remaining gap explicitly in COVERAGE.md; never relabel a whole workload as complete.

## Acceptance and evidence

Every slice has failing-first contract tests, PostgreSQL integration tests and real
local wire qualification. Exercise nonempty evidence, empty authorized collections,
404 versus denied access, malformed/unsupported queries, least permissions, revocation,
multiple pages, mutation between pages, two API instances, restart, injected 429 and
export/reset/upgrade. Do not count route registration alone as working coverage.

Use unmodified official Graph Python SDK/PowerShell and original pinned collectors
where they issue the relevant operations. Record endpoint-level successes/failures and
unsupported collector paths; retain existing MES JSON-output limitation and original
versus patched profiles. Feed uses direct HTTP if no suitable official SDK exists.
No qualification test may contact Microsoft tenant endpoints.

Final gate: all existing CI checks, added workload checks, clean default installation,
upgrade from baseline data, wheel/sdist packaging and a combined seeded collection run.
A local scale profile exercises 100,000 message/item metadata records, a 256 MiB streamed
object and eight concurrent clients across two API instances. Record latency, memory,
query plans and recovery; require complete IDs/hashes, no unbounded materialization,
no cross-tenant results and no lost acknowledged writes. Do not promise a throughput
number before measuring on the documented hardware.

Plans are proposed together for review. Approval permits Native execution of the ten
slices; routine implementation choices inside these boundaries do not require repeated
scope questions. New workloads or materially different authentication semantics do.
