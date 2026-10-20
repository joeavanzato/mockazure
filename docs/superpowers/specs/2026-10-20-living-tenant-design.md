# MockAzure: connected content, evidence, hunting, and identity lifecycle

Date: 2026-10-20
Status: approved by the user on 2026-10-20; implementation plan requires review.

## 1. Agreed direction

Expand the general-purpose Microsoft cloud emulator in four areas:

1. Connected business content and activity across the existing services.
2. Basic, reliable operation-to-evidence behavior.
3. Graph advanced hunting and correlated security data, excluding incident updates.
4. Identity and access lifecycle.

Collection failure simulation is deferred. Existing retry, paging, notification,
and recovery guarantees remain regression requirements; this program does not add
a new chaos-testing subsystem.

Continue using PostgreSQL, the existing service-oriented console, composable
tenant/data profiles, and real local API interactions. All runtime traffic stays
inside the lab. VTW remains untouched.

“Exhaustive” means a complete, tested implementation of the enumerated workflows,
with a published operation/property/permission matrix. It does not mean claiming
all Microsoft service behavior or silently accepting unsupported operations.

## 2. Approach and existing foundations

Recommended: extend the current generators, population runner, service adapters,
evidence jobs, and query evaluator. Add small service-specific modules where needed.

Alternatives considered:

- More independent fixtures would add volume quickly, but would not adequately
  test relationships, authorization changes, or actions producing evidence.
- A general simulation engine with autonomous agents and virtual scheduling would
  introduce unnecessary complexity for the requested basic evidence behavior.

The recommended approach combines deterministic historical population with finite,
explicit batches of real local service operations.

Inspected foundations:

- `population/generators`: versioned, bounded generators and service recipes.
- `profiles`: inheritance, ordered data layers, typed parameters and resource bindings.
- `population`: preview receipts, immutable runs, checkpoints and provenance.
- `evidence/clock.py`: evidence time is already separate from OAuth/continuation time.
- `evidence/sources.py`: durable projection jobs and source reconciliation.
- `monitor`: typed log tables and diagnostic delivery to Blob/Log Analytics.
- Existing bounded KQL parser/evaluator: joins, aggregation, dynamic values and budgets.
- Existing directory authorization, sessions, grants, roles and service state.

The work must preserve the difference between imported history and a service
operation. Importing an event cannot grant access or pretend an API call occurred.

## 3. Workstream A: connected content

### Shared organization model

Generate a deterministic organization graph containing departments, teams, projects,
locations, employees, managers, contractors, guests, administrators, applications,
devices, subscriptions, resource groups and workloads.

Use a stable internal identity registry and explicit cross-service mappings.
Microsoft service IDs are not assumed to be interchangeable. For example, directory,
Intune and Defender device identifiers retain their own formats and relationships.

Typed parameters control seed, reference time, history days, user count, department
count, projects, content volume, activity density and optional investigation layers.
Defaults remain suitable for a developer laptop. Bounded batch processing must
support larger profiles without materializing the whole tenant in memory.

### Content and operation matrix

| Area | Historical content | Operational workflow |
| --- | --- | --- |
| Directory | Departments, reporting lines, groups, owners, applications and devices | Create/update users; group and owner changes; assignments and grants |
| Mail | Folders, conversations, sender/recipient relationships, MIME, attachments, received/sent copies and rules | Compose/send/reply/forward through supported local mail APIs; attachment access; permitted rule changes |
| Calendar | Meetings, recurrence, attendees, responses and exceptions | Create/update/respond/cancel using existing local calendar behavior |
| SharePoint/OneDrive | Project sites, libraries, folder trees, documents, versions, authors and permissions | Upload/download/update/move/copy/delete and supported sharing/permission workflows |
| Teams | Teams, channels, chats, membership, threaded messages, mentions and document links | Add the common Graph v1.0 membership and message operations needed for the declared collaboration workflows |
| Intune | User/device relationships, detected software, policy assignments and observed compliance | Provision typed evidence; update observed state through explicit lab controls; validate existing Conditional Access behavior |
| Azure inventory | Subscriptions, resource groups, compute/network resources, storage, vaults and workspaces | Provision existing supported resources; exercise existing VM and permission operations |
| Storage | Blob objects, file shares/directories/files, table entities and queue messages | Read/write/update/delete through each existing data plane, including queue receive/complete |
| Key Vault | Synthetic secret names, versions, metadata and access relationships | Set/get/version/delete/recover and supported rotation workflows |
| Security | Identity/device/email/cloud telemetry and linked alert evidence | Populate hunting tables; collect read-only alerts and incidents |

Add missing endpoints required by these workflows rather than hiding a public
write behind an import and calling it operational support. Each new operation
must have a pinned Microsoft contract and a client test before coverage is claimed.

Teams operations must honor the documented delegated/application/RSC distinctions.
An app-only token must not gain ordinary message-send authority merely because a
fixture or migration permission exists. Team/site provisioning that is not needed
for the declared operations can remain explicit lab provisioning.

Intune device execution, enrollment, wipe, retire, and a full settings-policy engine
are outside this design. Observed compliance is synthetic evidence, not proof that
a device executed a policy.

### Content quality and consistency

- Use reusable, entirely synthetic business themes: finance, HR, engineering,
  sales, operations and support.
- Produce valid downloadable text, CSV, JSON, PDF and basic Office documents from
  deterministic local templates. No external content service or LLM is required.
- Derive attachment hashes and document metadata from the actual stored bytes.
- Preserve conversation and meeting relationships, project ownership, file links,
  user identities and device references across service representations.
- Keep shared underlying objects shared where the real services do; keep copies
  distinct where the real operation creates a copy.
- Validate references before apply. Removing a dependency must produce the declared
  conflict/tombstone behavior rather than leave unexplained broken references.
- Mixed languages, Unicode, empty optional values and realistic content sizes belong
  in ordinary content profiles, without creating a separate failure simulator.

### Profile layers and controls

Retain the current tenant/data/template document model. Add reusable layers:

- Organization and project baseline.
- Mail and calendar collaboration.
- SharePoint/OneDrive and Teams collaboration.
- Device and software estate.
- Azure application operations.
- Security telemetry.
- Joiner/mover/leaver and privileged access.
- Optional investigation overlays.

Provide small-business, departmental and enterprise-sized template presets. Counts
are configurable; “enterprise” is a dataset shape, not a throughput guarantee.

Support two clear execution choices:

1. Populate a specified historical window.
2. Run a finite activity batch with an action count and optional pacing.

Reuse preview, run progress, cancellation, resume and manifests. Expose these through
the existing CLI and console, including relationships and collection-ready results.
A run finishes when its declared work finishes; no always-on scheduler is introduced.

## 4. Workstream B: basic operation-to-evidence behavior

### Evidence contract

For each declared operation, record the source action, actor, target, time, outcome,
and safe correlation identifiers. Project only the evidence surfaces appropriate
to that action. Do not create one record in every service for every operation.

| Action family | Evidence to implement or verify |
| --- | --- |
| Authentication and session actions | Applicable sign-in/session outcomes and existing policy disclosure rules |
| Directory, grant and role changes | Directory audits with actor, target and changed properties |
| Mail and collaboration operations | Applicable Exchange/UAL/Management Activity records using sourced operation names |
| ARM mutations | Applicable Administrative Activity Logs |
| Storage operations | Correct per-service diagnostic operation names, success and supported denial/failure outcomes |
| Key Vault operations | AuditEvent records including supported authenticated failures |
| Declared identity/device/cloud activity | Eligible hunting projections where a documented semantic mapping exists |

Maintain a versioned mapping from operation to source fact, projection, schema,
availability and collection test. A source may have no mapping to a particular
surface; that is explicit.

Successful mutation facts must survive with the committed mutation. Failed-call
facts must survive rollback of the attempted mutation without converting a failure
into success. Authentication and tenant attribution must be established before
writing tenant-specific denied-call evidence.

Reuse current durable jobs and bounded retries. Projection retries must not create
duplicate source events. Record projection failures in existing run/job diagnostics.
No new event broker or general event-sourcing rewrite.

Exclude passwords, tokens, keys, reusable credential material and secret values
from evidence, manifests and browser previews.

### Time and controls

Reuse the existing evidence clock and delay/retention settings. Historical content
and hunting evaluation must use a documented, consistent lab reference time.
The console displays that time so a frozen dataset is not mistaken for live data.

OAuth, sessions, callback credentials, continuation expiry and effective privilege
expiry retain their security time semantics. Advancing an evidence clock must not
extend credentials or activate privileges.

Keep the controls limited to reference time, historical window, action count,
pacing and existing run cancellation/resume. No probabilistic behavior engine,
distributed virtual clock, or new collection fault injection.

## 5. Workstream C: Graph hunting

### Public API

Implement `POST /v1.0/security/runHuntingQuery` against actual stored tenant data,
with the Graph hunting request/response contract and `ThreatHunting.Read.All`.
Support documented Query/Timespan behavior and explicitly qualify workspace
selection. Do not accidentally expose arbitrary Log Analytics workspaces.

Use a separate hunting language/service profile over reusable KQL internals.
Do not reuse ARM/Logs authorization or return their response envelopes.
Validate table and column names even when the source has no rows.
Unsupported syntax and budget exhaustion produce explicit structured errors.

### Table scope

| Group | Tables targeted |
| --- | --- |
| Identity | IdentityInfo, IdentityLogonEvents, IdentityDirectoryEvents, IdentityQueryEvents |
| Email | EmailEvents, EmailAttachmentInfo, EmailUrlInfo, EmailPostDeliveryEvents, UrlClickEvents |
| Devices | DeviceInfo, DeviceNetworkInfo, DeviceLogonEvents, DeviceProcessEvents, DeviceNetworkEvents, DeviceFileEvents, DeviceRegistryEvents, DeviceEvents |
| Cloud activity | CloudAppEvents |
| Security correlation | AlertInfo, AlertEvidence |

Pin each table's official columns, types, availability and documented action types.
Preview tables are not silently presented as stable. Entra-specific sign-in hunting
tables require a separately identified schema/version profile if included; Graph
v1.0 transport does not make every underlying hunting table generally available.

Generate correlated fixtures for all 20 listed tables. Preserve the distinction
between tables populated from local service outcomes and synthetic sensor telemetry.
A cloud API call alone cannot prove that a host process executed or a URL was clicked.

Reuse the existing bounded query operators and add only those necessary for the
published query cookbook and client compatibility cases. Include identity-to-device,
email-to-attachment, alert-to-entity and user-to-cloud-activity joins.

Expose table/column discovery in the lab catalog and console. Keep lab-specific
discovery separate from Microsoft public APIs.

### Security data and limits

Enforce tenant isolation, current permission checks and documented service entitlement
requirements before loading query inputs. Model delegated data restrictions explicitly;
do not advertise full Defender unified RBAC without implementing it.

Keep read-only Graph alert and incident collection working with correctly linked
identifiers. Do not implement incident updates, comments, assignment, response actions
or automatic incident-management workflows.

No general detection engine, host sensor, arbitrary KQL plugin, external data fetch,
or complete Defender platform emulation.

## 6. Workstream D: identity and access lifecycle

| Workflow | Proposed scope and observable result |
| --- | --- |
| Joiner | Create user; set profile/manager; assign supported licenses, groups and application access; provision lab workload relationships |
| Mover | Change department/manager/group/application access; verify new access and revoked access through real requests |
| Leaver | Disable sign-in, remove selected grants/roles/memberships, revoke sessions and optionally delete the identity; retain audit history |
| Guests | Graph invitations with pending/accepted state, local-only redemption and supported guest access; no outbound invitation delivery or external identity provider |
| Deleted objects | List/get supported deleted users, applications and service principals; restore/permanently delete; conflict handling and retention |
| Groups | Existing static group/owner workflows; group deletion/restoration only for Microsoft-supported group types implemented by the emulator |
| App credentials | Existing secret rotation plus certificate addKey/removeKey actions with proof-of-possession validation and actual token-acquisition effects |
| Session revocation | Graph revokeSignInSessions, refresh/session invalidation and documented guest limits; do not promise immediate invalidation of every access token |
| Delegated/application grants | Consent grant/revocation and ownership transitions with current access checks |
| Entra PIM | Built-in tenant-scoped role eligibility, assignment requests, activation/deactivation, schedules/instances and expiry |
| Azure access | Existing scoped RBAC assignment/removal exercised against ARM, Storage and Key Vault; no new Azure PIM subsystem |

Entra PIM is a real authorization change, not just rows that look like schedules.
Implement the policy constraints needed for the supported paths: eligible principal,
allowed activation duration, justification, trusted MFA state where required and
explicit approval-required rejection until an approval workflow exists.

Use Graph role-management policy resources for supported policy reads/changes.
Unsupported rules must not be ignored. Group-based PIM, custom/scoped Entra roles,
access reviews and entitlement-management packages are deferred.

Scheduled privilege expiry is checked by authorization as well as background cleanup;
a stopped worker cannot leave an expired grant effective.

Directory soft deletion needs a migration and dependency contract. Preserve
historical audit references; block deleted identities from authenticating. Restore
only the documented dependencies for each type. Restoring an application must not
implicitly restore its service principal or resurrect revoked sessions.

Guest redemption uses a clearly named local management/interaction flow where
Microsoft has no equivalent Graph operation. It does not imitate an unsupported
public Microsoft redemption endpoint.

## 7. Schemas, implementation boundaries and compatibility

Before implementation of each public operation:

1. Pin the official v1.0 CSDL/OpenAPI and Microsoft operation documentation where
   available; pin Defender table references separately.
2. Record method/path, request and response fields, enum values, permissions,
   role/license constraints and unsupported variants.
3. Define canonical state ownership and dependent relationships.
4. Define success, authorization failure, validation failure and lifecycle behavior.
5. Write contract and real-client acceptance cases.

Graph schemas alone do not describe every authorization or runtime behavior; the
operation documentation and explicit local limitations are part of the contract.
Where official artifacts conflict, resolve and record the discrepancy before
claiming compatibility.

Keep additive migrations safe for existing local data. Preserve old profile bundles
and generator versions when feasible; never silently reinterpret a resumable run.
Existing namespaces require explicit migration or a fresh namespace when generation
semantics change.

## 8. Delivery sequence

Use one coordinated program with four reviewable implementation phases:

1. Connected organization/content model, generators, corpus and missing collaboration
   operations; extend the console and CLI.
2. Basic evidence completion and operation-to-evidence mapping, including denied
   Storage/Key Vault diagnostics.
3. Identity lifecycle and bounded Entra PIM, with evidence and population recipes.
4. Graph hunting, all listed table generators, supported source projections,
   query cookbook and combined original-client qualification.

Hunting schema acquisition can happen earlier, but its final qualification consumes
the connected content and identity/evidence workflows.

Each phase includes its own coverage/documentation updates and acceptance evidence.
Do not leave qualification and migration work to an unrelated cleanup milestone.

## 9. Acceptance and deliverables

- A dependency/operation matrix maps every workflow above to its API, fixture mode,
  permissions, evidence outputs, schema reference and automated acceptance case.
- Repeated generation with the same namespace/seed is stable and does not silently
  duplicate data. Separate namespaces remain independent.
- Every generated relationship resolves; downloaded bytes match declared hashes.
- Original Graph SDK/PowerShell, Exchange and Azure clients exercise the applicable
  workflows without replacement collector commands.
- Tenant isolation and permission/license failures are tested for each new surface.
- Every listed hunting table has schema/type checks, generated data, at least one
  meaningful query, and relationship checks where applicable.
- Hunting joins cannot include unauthorized input; unknown queries do not return
  misleading empty success.
- Privilege activation/expiry, revocation and deletion/restoration have behavioral
  tests, not only response-shape checks.
- Existing restart/resume guarantees are regression-tested where changes affect them.
  This does not expand into the deferred failure-simulation product.
- Qualify a laptop-sized complete tenant and a larger bounded dataset. Record counts,
  bytes, memory configuration and timing; do not invent a throughput target.
- Update COVERAGE.md, the runtime catalog, getting-started/profile guides, query
  cookbook and a reproducible results report.
- Distinguish local passing checks from hosted CI. Any hosted infrastructure blocker
  remains visible and does not become an assumed pass.

## 10. Official references reviewed

- [Graph hunting API](https://learn.microsoft.com/en-us/graph/api/security-security-runhuntingquery?view=graph-rest-1.0)
- [Defender hunting schema](https://learn.microsoft.com/en-us/defender-xdr/advanced-hunting-schema-tables)
- [IdentityInfo](https://learn.microsoft.com/en-us/defender-xdr/advanced-hunting-identityinfo-table)
- [Entra sign-in hunting table, currently marked preview](https://learn.microsoft.com/en-us/defender-xdr/advanced-hunting-entraidsigninevents-table)
- [Entra PIM APIs](https://learn.microsoft.com/en-us/graph/api/resources/privilegedidentitymanagementv3-overview?view=graph-rest-1.0)
- [Session revocation](https://learn.microsoft.com/en-us/graph/api/user-revokesigninsessions?view=graph-rest-1.0)
- [Guest invitations](https://learn.microsoft.com/en-us/graph/api/invitation-post?view=graph-rest-1.0)
- [Deleted-object restoration](https://learn.microsoft.com/en-us/graph/api/directory-deleteditems-restore?view=graph-rest-1.0)
- [Application certificate rollover](https://learn.microsoft.com/en-us/graph/api/application-addkey?view=graph-rest-1.0)
- [Teams channel message creation](https://learn.microsoft.com/en-us/graph/api/channel-post-messages?view=graph-rest-1.0)
- [Teams chat message creation](https://learn.microsoft.com/en-us/graph/api/chat-post-messages?view=graph-rest-1.0)
- [Key Vault logging](https://learn.microsoft.com/en-us/azure/key-vault/general/howto-logging)

These references support design preparation; they are not yet a pinned implementation
contract bundle. Acquiring and verifying that bundle is a required implementation-plan
step.
