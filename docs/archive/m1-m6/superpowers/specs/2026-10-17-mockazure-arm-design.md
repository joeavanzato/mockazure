# M5: Azure management and Activity Logs

## Authority, intent and predecessor

The user approved broad, stateful Microsoft cloud API emulation for evidence
collection and operational permissions, PostgreSQL, Native execution, and
continuing all defined milestones overnight with reasonable decisions delegated.
This written design is adopted under that delegation, not described as separately
human-reviewed. M4 API repairs are the predecessor; its original Hawk JSON and
MES trace timestamp output gates remain unresolved. They remain visible in M6.

M5 adds realistic local ARM inventory, inherited scoped authorization, selected
management operations and Activity Log collection. It does not pretend that
inventory records provision running virtual machines or storage services.

Three approaches considered: independent canned ARM responses are too weak for
authorization and evidence; implementing every Azure resource provider exceeds
the approved milestone; a canonical inventory with genuine management operations
and explicit provider limits supports the intended tools. Choose the third.

## Isolation and contracts

Use D:/projects/mockazure/.worktrees/arm, branch feat/azure-management, based on
the repaired M4 branch. Preserve all predecessor checkouts and runtimes.
Python3.12+, PostgreSQL18, existing FastAPI/SQLAlchemy/jobs/evidence infrastructure.
No VTW access, real tenant calls, host trust/DNS/firewall changes, secret copies,
package publication or merges. Public official source acquisition is allowed.
Pin exact official Azure REST Swagger revision and SHA256 files before coding
each operation; reference actual Microsoft REST docs and role definitions.

Own test runtime: project mockazure-arm-tests, PostgreSQL55443,
networks10.231.96/24 and10.231.97/24. Qualification: project mockazure-m5,
HTTPS36443, PostgreSQL55444, temporary native36444/36445, managed identity36446,
networks10.231.98/24 and10.231.99/24. Verify availability before binding.
Only verified named public archives may be reused from predecessors; create fresh
runtime CA, tokens and configuration. Tests and reports are source-bound.

## Supported protocol profile

Explicit api-version allowlist:
- Subscriptions/tenants:2020-01-01 and2021-01-01 as required by pinned Az/MES.
- Resource groups, generic inventory and tags:2021-04-01.
- Role definitions and assignments:2022-04-01.
- Activity Logs:2015-04-01.
Reject undeclared versions and query parameters, duplicate query keys and
unsupported provider routes with explicit ARM errors, never fabricated empty200.

GET /tenants exposes the authenticated local tenant only, using the actual
TenantIdDescription schema. GET /subscriptions lists accessible subscriptions;
GET /subscriptions/{id} supports original Get-AzSubscription. Current identity
and ARM audience are checked, and disabled subscriptions reject writes.

GET resourcegroups and GET a named group; PUT creates/updates, PATCH updates
supported fields; DELETE performs durable asynchronous deletion with a local
Location polling URL and Retry-After. Poll responses/statuses follow the pinned
REST contract. Group deletion removes only its own inventory and descendant role
assignments, and emits one completion event. No forceDeletionTypes implementation:
reject that parameter. Failed jobs remain observable and stale leases cannot commit.

GET /subscriptions/{id}/resources and group-scoped /resources return canonical
GenericResourceExpanded inventory. Support top1..1000/page cap100; documented
eq/ne filters for name,resourceType,resourceGroup,location; AND/OR and documented
tagName/tagValue pair; expand createdTime,changedTime,provisioningState.
Reject unsupported documented expressions explicitly. A tag-filtered response
omits tags as the actual contract specifies.
Resource-specific compute/storage/network methods remain explicit unsupported
routes; listing a provider type does not claim its deployment/data-plane API.

GET/PUT/PATCH/DELETE {scope}/providers/Microsoft.Resources/tags/default operates
on subscription, group or existing inventory resource. PUT replaces; PATCH
Merge/Replace/Delete follows actual name/value matching. Maximum50 tags,
case-insensitive tag keys with case-preserved output, case-sensitive values,
bounded names/values from official rules. Concurrent merges serialize and do not
lose independent changes. State updates and immutable evidence share a transaction.

## Canonical persistence and administration

ResourceGroup belongs to tenant/subscription, has case-insensitive unique name,
location,managedBy,tags,provisioning state,created/changed timestamps and revision.
Generic inventory belongs to tenant/subscription/group, has a validated canonical
resource ID, namespace/type/name fields, optional properties/sku/plan/identity/
extendedLocation from the pinned generic schema, tags and timestamps. Store JSONB
only for flexible provider facts; filter/scope dimensions are indexed typed columns.
Admin import is bounded1000 records per call, deterministic/idempotent by resource
ID, and rejects tenant/subscription/group inconsistencies or credential-shaped
fields. It seeds inventory, not native successful cloud-operation evidence.

Canonical scope parsing compares case-insensitively at segment boundaries:
subscription, group, resource (including nested resource pairs).
Reject empty/traversal/double-encoded/separator ambiguity. Persist canonical keys
separately from original display IDs. A group named a never grants access to ab.
All lookups and compound foreign keys enforce tenant/subscription ownership.
Never infer application client ID from a service-principal object ID.

Keep existing /_mock/.../subscriptions and legacy Reader assignment endpoints.
Migration converts existing Reader rows to effective subscription Reader grants;
legacy updates/deletes and public role assignments share a single authority,
without double grants or resurrection after revocation. Admin endpoints can seed
groups/resources and bootstrap Owner/RBAC for local user/SP/group object IDs.

## Authorization and RBAC

Support immutable built-in definitions Reader,Contributor,Owner and User Access
Administrator with official IDs/actions/notActions. GET/list definitions and
GET/list/PUT/DELETE assignments at supported scopes. Assignments reference
tenant-local current User,ServicePrincipal or Group object IDs, infer/validate
principalType, and use a subscription-qualified roleDefinitionId. Reject
conditional/delegated assignments and custom role writes explicitly.
Repeat identical assignment PUT is idempotent; conflicting immutable assignment
identity returns conflict. Duplicate scope/principal/role assignments are bounded
by a database uniqueness constraint. Delete is scoped and audited.

Role permission union: actions minus notActions within each role, then union
across applicable roles; notActions is not an explicit deny of another grant.
Subscription→group→resource inheritance, no upward escalation. Group assignments
include current transitive security-group membership for users/SPs with cycle
handling. Disabled/deleted caller identities and removed memberships/grants
take effect on every operation/page/poll. Graph permissions and Entra directory
roles alone never grant ARM management access.
Reader cannot mutate; Contributor cannot change RBAC; Owner can; User Access
Administrator can change RBAC but not general resource settings.

Lists filter records by effective scope before paging/counting. Collection
authorization may enumerate only readable descendants; inaccessible objects are
never exposed by a privileged continuation from another caller. Role assignment
listing follows explicit atScope()/principalId filtering from the pinned contract,
with unsupported filters rejected.

## Evidence and pagination

Native group/tag/assignment changes append EvidenceSource(kind=arm) atomically.
Project through the existing fenced worker pipeline to family activityLog using
the actual EventData schema: eventDataId/id, eventTimestamp/submissionTimestamp,
caller,authorization(action/scope),correlationId,resourceId/resourceGroupName,
subscriptionId,resourceProviderName,operationName,status,subStatus,category,
level,properties and claims only when captured. No fabricated IP or token claims.
Retain immutable actor/target facts across later renames/deletion. No-op operations
do not create successful-change events. Async group deletion emits its terminal
success/failure with the same correlation, without pretending request acceptance
completed deletion.

Historical Activity Log import/export participates in shared evidence idempotency,
retention,clock/reset and generation semantics. Validate subscription/resource
scope and actual EventData payload. Protect secrets. Do not make Graph evidence
endpoints accept or disclose ARM family records.

GET /subscriptions/{id}/providers/Microsoft.Insights/eventtypes/management/values
requires ARM RBAC Microsoft.Insights/eventtypes/values/read, supports actual
time-bound filter grammar,resourceGroupName,resourceUri,resourceProvider,
correlationId and documented select. Filter semantics and DateTime compatibility
are pinned before implementation. Original MES culture-rendered time literals
must be observed; if accepted, use one explicitly bounded UTC/en-US form,
not arbitrary locale guesses.
SQL filters and keyset order(eventTimestamp,eventDataId) handle timestamp ties.
Opaque continuations bind tenant/caller/permission claims/version/query,
capture publication/generation watermark, expire after1hour, reauthorize current
RBAC and preserve retention needed by live cursors. Reset/expiry fails explicitly.
Inventory paging is a bounded live view, not a fabricated point-in-time snapshot;
document concurrent deletion/update behavior and prevent duplicates for stable IDs.
Use shared fault injection for429/Retry-After and5xx; never successful empty output
for denied/unsupported requests.

## Verification and handoff

Unit/integration tests cover canonical paths, source schemas, all scope boundaries,
app/delegated/group RBAC and revocation, migration, concurrent tags/assignments,
cross-tenant IDs, disabled identities/subscriptions, async worker lease loss,
restart and evidence atomicity, filters/select/paging/retention.
Real Az.Accounts plus required pinned Az.Resources commands must exercise local
discovery, groups/tags/RBAC; original command implementations remain unchanged.
Use actual Python Azure management SDK where the declared profile supports it,
with a local-only token transport and explicit local base URL; no public fallback.

Original MES Get-ActivityLogs must retrieve1003 distinct historical events plus
native mutation evidence in both app/delegated modes, through both discovered and
explicit subscription paths. Every JSON artifact must parse, contain exact IDs,
timestamps,actions/scopes and no denied records. Use original helpers/initializer;
only previously allowed transport remapping/telemetry suppression.
Verify clean wheel, native/container parity, API/worker/PostgreSQL restarts, two
workers/replicas and source/archive/output hashes. Record failures truthfully and
carry predecessor blockers. One fresh final reviewer, one consolidated fix pass.
Push and attach a separate feature PR, no merge; continue M6.

## Reference entry points

- https://learn.microsoft.com/en-us/rest/api/resources/resources/list?view=rest-resources-2021-04-01
- https://learn.microsoft.com/en-us/rest/api/resources/resource-groups/create-or-update?view=rest-resources-2021-04-01
- https://learn.microsoft.com/en-us/rest/api/resources/resource-groups/delete?view=rest-resources-2021-04-01
- https://learn.microsoft.com/en-us/rest/api/resources/tags/update-at-scope?view=rest-resources-2021-04-01
- https://learn.microsoft.com/en-us/rest/api/authorization/role-assignments/create?view=rest-authorization-2022-04-01
- https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged
- https://learn.microsoft.com/en-us/rest/api/monitor/activity-logs/list?view=rest-monitor-2015-04-01
