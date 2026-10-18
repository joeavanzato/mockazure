# M5 ARM contract reference

Status: M5 public inventory, RBAC, group/tag mutations and Activity Log routes are
implemented. Supported API versions and qualification boundaries are recorded below.

Official Swagger revision:
`Azure/azure-rest-api-specs@ae348cc71f54a61f5d17472052cfef279a23db6b`.
The source repository now separates resources/subscriptions/Authorization/Insights
into service folders; old flat paths return404. Acquisition uses verified paths,
not undocumented substitute schemas. Twelve repository files include six root
documents, common schema dependencies and the MIT licence.
Microsoft Learn's general/privileged built-in role pages are separately pinned
by SHA256. The two derived catalogs are hash-bound too:16 reference entries.

Reproduce with `python tools/acquire_arm_contracts.py`. Published role source
changes require an explicit reference update; cached source snapshots are verified
before reuse. Runtime validation is entirely offline. Package schema validation
does not invoke Microsoft services.

## Declared profile

| Surface | API versions | Operations |
| --- | --- | --- |
| Tenant/subscription discovery |2020-01-01,2021-01-01|Tenant list,subscription list/get|
| Resource groups |2021-04-01|List/get/create-or-update/update/delete|
| Generic resources |2021-04-01|Subscription/group inventory lists|
| Tags at scope |2021-04-01|Get/replace/merge-selective-delete/delete|
| Role definitions |2022-04-01|List/get|
| Role assignments |2022-04-01|Scope list/get/create/delete|
| Activity Logs |2015-04-01|List/filter/select/paging|

The catalog derives HTTP methods, path templates,response schemas/statuses and
operation IDs from those exact Swagger files. It deliberately does not advertise
generic resource create/update/deployment or arbitrary provider operations.

## Scope and schema rules

Canonical keys lowercase whole path segments while preserving display IDs.
Subscription UUIDs use canonical hyphenated form; resource-group names obey the
bounded actual naming shape and90-character maximum. Provider names require
namespace segments and resource types/names occur in pairs. Subscription and
resource-group ancestors and nested resources are explicit. Scope containment
compares full ancestor keys:group a never contains group ab.

Control characters,traversal,empty segments,relative IDs,backslashes,query/fragment
syntax and percent-encoded scope inputs fail. HTTP routes must also inspect raw
paths to reject ambiguous encoded separators before framework decoding (Task3).
Unknown/future versions and operations fail explicitly.
Validators consume actual Swagger allOf/refs/properties/required/types and bounded
JSON. Unknown named properties fail; declared dynamic provider dictionaries
remain flexible. DateTime fields require timezone. Extensible x-ms-enum strings
remain extensible as published; operational code must still enforce its supported
values. A structural schema is not a grant of permission or operational support.

Reader,Contributor,Owner and User Access Administrator definitions use the actual
published role IDs and complete actions/notActions. Permission evaluation follows
in Task2:subtract within a role,then union roles. No data-plane grant is implied.

## Verification and predecessor limits

Task1 RED:15 tests failed for absent contract/scope modules. Initial implementation
passed13 with2 missing metadata cases; after deriving catalogs,15 passed0.13s.
The copied M5 unit baseline passed243 tests before implementation.
M4 draft PR8 retains unresolved original Hawk investigation JSON and MES trace CSV
precision gates. M5/M6 do not imply a waiver.

Sources:
- https://github.com/Azure/azure-rest-api-specs/tree/ae348cc71f54a61f5d17472052cfef279a23db6b
- https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general
- https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged


## M5 operational ARM profile (Task3)

Discovery supports2020-01-01/2021-01-01. Inventory/groups/tags use2021-04-01.
Inventory filtering uses PostgreSQL predicates and effective inherited grants before
pagination. Opaque continuations bind tenant, current caller, token scope set,
API version, path and directory generation, expire after one hour, and reject
inventory revision changes rather than silently mixing pages across mutations.
This is a bounded restart-on-change profile, not an immutable inventory snapshot.
No generic provider deployment endpoints return fabricated success.

Resource-group mutations and tag updates serialize on the subscription after
security/evidence locks. Native arm source facts share their transaction; no-op
writes suppress facts. Activity Log publication is supplied by Task5.
Group deletion uses a durable job and a tenant-owned FK, local Location/Retry-After,
and lease/generation fences. It returns202 while running,200 on completion per
the pinned group DELETE response contract, and an ARM error on terminal failure.
Reset cancels old-generation deletion without deleting inventory or publishing
a success. Explicit DELETE retries a terminally failed operation.

Tag names compare without case; values compare with case. Merge retains existing
name spelling. Empty Delete values mean name-only removal, inferred from the
official CLI name-only serializer combined with the documented name-or-pair
Delete semantics. Null values remain outside the pinned string-valued schema.
Lowercase tagname/tagvalue filters are emitted by the same official CLI.
The following immutable official source references were inspected before adding
these compatibility behaviors (no Azure service requests were made):

- https://github.com/Azure/azure-cli/blob/da4c0548a2725ea3d08b1d94b8e7fa1b0b07256e/src/azure-cli/azure/cli/command_modules/resource/custom.py
  SHA256 f7ae7ff2a650cac62bd7c2c7589cd00cc120b02ccc96c27d50824f7fc9694ef5.

- https://github.com/Azure/azure-cli/blob/da4c0548a2725ea3d08b1d94b8e7fa1b0b07256e/src/azure-cli-core/azure/cli/core/commands/validators.py
  SHA256 4fccf04fab3aeb0cb73adec72eb66b143ec84658f9ceeb20e1f553ddee392b7d.

REST references:
- https://learn.microsoft.com/en-us/rest/api/resources/tags/update-at-scope?view=rest-resources-2021-04-01
- https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/async-operations

Raw percent-encoded ARM paths are explicitly rejected; canonical comparison is
case-insensitive at segment boundaries. This deliberately excludes some otherwise
valid encoded Azure names until a nonambiguous encoding profile is implemented.

## Public RBAC (Task4)

Authorization2022-04-01 serves the 20 pinned built-in role definitions and
canonical role assignment GET/list/PUT/DELETE. Principal IDs are local object
IDs; omitted principalType is inferred, supplied types must match. Scope/principal/
role identity is immutable; equivalent new IDs conflict. Description updates
are bounded1024 characters. No-op writes create no evidence. Legacy administration
revokes these same rows.

atScope() includes exact/inherited assignments; principalId equality includes
matching ancestors/descendants within the subscription. Current role access is
required at the requested scope on each page. RBAC continuations use the published
$skipToken parameter, a100-item cap, one-hour expiry, identity/generation/revision
binding and current grant checks. Removed roles or group edges revoke access
immediately on the next operation/page/poll.

Ambiguous official schema names require an explicit provider namespace:
RoleDefinition in resource-management2021 differs from authorization2022.
Conditional/delegated grants, foreign principals and custom-role writes fail
explicitly; no success response implies support for those operations.

## Activity Logs (Task5)

The2015-04-01 management event endpoint uses the pinned EventData schema and
two exact upstream examples. The selected example explicitly requests id, so
id is accepted alongside the SelectParameter field list; unselected fields
are omitted. No undocumented category/resourceType selection is implied.

Import requires owned subscription, matching UUID eventDataId and ARM event id,
matching timestamp ticks/subscription/scope, and exact submission ordering.
The seventh fractional digit survives import, SQL filtering, paging and export.
PostgreSQL datetime columns retain microsecond metadata; separate integer100ns
dimensions govern public Activity visibility and ordering. Public typed authorization
is evidence, while credential-bearing properties remain rejected. Historical
deleted targets need not exist in current inventory.

Native group/tag/RBAC/completed-deletion facts project through the same generation,
publication, receipt, retention and export machinery. Captured caller/action/scope/
correlation/status are retained. Unobserved IP addresses and claims are omitted.
A terminal deletion error or exhausted third worker lease commits Failed evidence
with failed job state under security→evidence→subscription→job locks; unsuccessful
attempts never produce Succeeded evidence. Evidence-write failure rolls back the
terminal transition. The reaper finalizes at most one exhausted ARM operation per
claim call, bounding its lock footprint. Old-generation cancellation emits no event
into the new generation and restores the affected group for explicit retry.

The filter requires eventTimestamp ge; optional le and one of resourceGroupName,
resourceUri, resourceProvider or correlationId equality follow the pinned grammar.
Other operators/combination forms fail explicitly. Scope and dimension comparison
is case-insensitive. Rows are permission-filtered in SQL before the100-event cap,
ordered by exact ticks then UUID. Five partial indexes cover time and the four
dimensions. The one-hour opaque handle binds current identity/token scopes/path/
version/generation and an immutable publication/time/retention snapshot. Every page
rechecks current grants; revocation can shrink or deny the remaining result, never
continue disclosing revoked data. Later publication is excluded from the snapshot.
Reset invalidates the handle; active handles pin retained versions during cleanup.

The two additional examples are hash-bound in references.json:
GetActivityLogsFiltered.json a6e73245b24d7434c7eae5dd7e8a0c08edf5b70c032988c72243c6364b55f950;
GetActivityLogsFilteredAndSelected.json e4c6661497a1c1cda8ccaee7a91aa165de5043306e33b370d5fc2f94472b8a57.
Both use Azure/azure-rest-api-specs ae348cc71f54a61f5d17472052cfef279a23db6b.

## Observed original-client corrections (Task6)

Azure Python resource SDK23.4.0 rejected tag DELETE204; the pinned2021-04-01
operation declares200, now returned. Actual Az.Accounts4.0.2 follows an empty
subscription nextLink and fails with an empty URI. Terminal tenant/subscription
pages now omit nextLink, following Microsoft's Azure REST API Guidelines
(https://github.com/microsoft/api-guidelines/blob/vNext/azure/Guidelines.md)
and matching the actual original client. The older subscription Swagger marks
nextLink required without a terminal-page representation; its illustrative
examples use a placeholder continuation. This specific wire-profile correction
is explicit rather than modifying the vendored schema or replacing SDK paging.


## Observed original Azure client contracts

Az.Resources7.8.0/Accounts4.0.2 uses resource groups2021-04-01,
tags2024-07-01, role definitions2022-05-01-preview, and role assignments2020-08-01-preview. These later operations
have their actual schemas and dependencies pinned at the same Azure REST revision;
the operation catalogue records version-specific sources. Tag types are checked
identical to2021 before sharing validation. Preview role definitions are validated
against their own schema in regression tests. Other operations do not gain those
versions implicitly.

Original generated scope clients emit a double leading slash for tag and RBAC
URLs. The HTTP boundary removes exactly one leading slash only for those known
operation shapes. Interior duplicates, encoded separators, and unowned scopes
remain rejected. Resource-group HEAD returns204/404 after current authorization.
Original Set-AzResourceGroup sends its unchanged name and an empty properties
object; both published Patchable fields are accepted, while rename attempts and
provisioningState writes fail.

Original MES Get-ActivityLogs at revision47565f7842784cd3667ff630e3dccb49fa3408ed
interpolates UTC en-US dates as MM/dd/yyyy HH:mm:ss. Only this additional query
literal grammar is accepted; imported EventData still requires explicit ISO8601
instants and retains seven fractional digits. The original JSON output preserves
all1003 seeded identities and exact timestamps. Its summary prints a CSV filename
from a shared helper, but the actual Activity output is a JSON array in a .json
file; qualification verifies the actual artifact.

Local admin Activity fault injection is scoped to tenant/subscription/client,
one-shot, durable across replicas, and returns ARM429 or503 with Retry-After. A denied
call neither consumes the fault nor masks its authorization error. It provides
repeatable retry qualification; it is not a claim to reproduce Azure's dynamic
service-wide quotas.

Hash-bound derived role/operation metadata uses UTF-8 and canonical LF bytes.
Vendored official files retain their original bytes.


Original Az qualifies built-in role-definition IDs at its assignment scope.
References at an existing local ancestor are accepted; foreign subscriptions,
unrelated scopes and nonexistent scopes are rejected. Stored role identity remains
the canonical immutable built-in UUID, so this compatibility form adds no authority.


## Provider discovery and data permissions

Provider list/get/register/unregister use the actual Microsoft.Resources 2021-04-01
contract. Five profiles cover Storage, KeyVault, Compute, Network and
OperationalInsights; metadata lists selected versions and the local eastus/westus2
locations. New subscriptions begin NotRegistered. Local registration completes
synchronously. Unregister rejects a namespace with current inventory; repeated
unchanged state does not create duplicate successful facts. Consent properties,
metadata/alias expansion and tenant-level provider APIs are unsupported.
Discovery metadata is a profile catalog, not proof that every provider operation
has a handler. Unknown paths/versions fail before inventory mutation. Subsequent
Storage/resource tasks implement the separately cataloged resource operations.

Twenty official built-in definitions include the original four control roles,
Blob Owner/Contributor/Reader, Queue Contributor/Reader/Message Processor/Message
Sender, Table Contributor/Reader, Files Privileged Contributor/Reader, and Key Vault
Administrator/Contributor/Reader/Secrets Officer/Secrets User. Their exact UUIDs,
actions and exclusions come from hashed Microsoft Learn sources. Conditional roles
such as Key Vault Data Access Administrator are excluded until their condition
language can be enforced. No custom-role write support is implied.

Data authorization requires the correct Storage or Key Vault service audience,
current application/service principal/user and current scoped assignments, including
transitive current security-group membership. It subtracts notDataActions within
each role before union; a different role may grant the excluded action. Contributor
and Owner control-plane wildcards alone grant no data action. Service handlers still
own network, service-specific permission modes and object authorization. The internal
check does not itself expose a Storage/Key Vault data endpoint or mint tokens; those
are delivered by their owning tasks.

Migration 057 stores subscription-bound provider state and generation-bound internal
service events. State and native Activity Log facts commit together. Identical event
IDs are idempotent; different facts under the same ID fail. These internal envelopes
are not a Microsoft wire schema. Diagnostics delivery is added in its own phase.

Authenticated local GET/PUT `/_mock/tenants/{tenant}/cloud/providerState` exports or
atomically replaces registrations/events; empty lists reset this state and advance
the generation. The local fixture limits are 5,000 registration rows and 10,000
events; exports exceeding them fail explicitly. All subscriptions must belong to the
tenant. Historical events may refer to deleted resources in those subscriptions.
Restored events are ineligible for publication; they never revive worker/delivery
authority. Secret-bearing payload fields are rejected. Bounded internal pruning
removes only old ineligible events, preserving undelivered native events. B4 connects
these contributors to the combined resource lifecycle. Migration downgrade refuses
nonempty provider/event stores. PostgreSQL and application restarts preserve state.

B1 has direct signed-token HTTP and database integration coverage; the original
management SDK/module matrix is completed in B4 alongside usable account/resource
workflows. No new four-ecosystem qualification is claimed by this foundation alone.


### Storage account management (B2)

Storage account operations use the pinned Microsoft.Storage **2025-06-01** Swagger,
including request/response definitions and referenced common types. The provider
catalog lists 17 implemented operations: subscription/group lists, check-name,
account GET/PUT/PATCH/DELETE, listKeys/regenerateKey, and GET/PUT for four service
property resources. PUT completes synchronously with **200**, as permitted by the
official create operation. Unsupported versions, queries (including `$expand`),
read-only input fields, and operations fail explicitly.

Register Microsoft.Storage before creating accounts. Supported kinds are Storage,
StorageV2 and BlobStorage with Standard LRS/GRS/RAGRS/ZRS/GZRS/RAGZRS; FileStorage and
BlockBlobStorage use Premium LRS/ZRS. Locations are eastus/westus2. Kind/location
cannot change in this profile. The local limit is 1,000 operational accounts per
tenant; lists are complete within that bound. Names are instance-global. Current
scoped ARM actions govern every operation; Reader cannot retrieve or rotate keys.
Control-plane grants do not grant Storage data actions.

Supported account settings include tags (15, 128-character names), access tier,
public network access, IP rules, Shared Key/public-blob/OAuth defaults, HTTPS/TLS
minimum settings, and large-file-share enablement. HNS/NFS/SFTP/local-user features,
customer-managed encryption, identity and network security perimeter inputs are
rejected. TLS 1.3 is rejected despite its published enum because the same Microsoft
contract explicitly describes it as unsupported. The local default minimum is
TLS1_2. Replication/SKU/tier are stored configuration; no Azure infrastructure is
provisioned. Service-property GET/PUT supports CORS configuration only at this stage;
retention/versioning enforcement arrives with the data planes.

`properties.primaryEndpoints` advertises four separate configured local HTTPS
origins using development-style **/account/** path addressing. The default ports
are the login port +1 through +5 for Blob, Files, Queue, Table and Key Vault; a
default native instance therefore uses 8444..8448. Explicit profiles can override
the origins and `data_listen_ports` can override native listeners. Native serve
binds all planned ports; a collision fails startup. Compose ingress forwards these
five services to the API; `init --compose` adds the corresponding port metadata
without rotating credentials, CA or database storage. This is a local endpoint
adaptation, not Microsoft public DNS. Data-plane operations remain independently
tracked in phases C/D; advertising an endpoint does not implement blob/file IO.

Network evaluation uses the observed peer IP only. Forwarded-address, private-endpoint
and Azure-service headers cannot grant trust. PublicNetworkAccess Disabled denies
public data access; default Deny permits only matching configured IP ranges.
Private/loopback IP rules are an explicit local-test adaptation. VNet/resource rules
and AzureServices bypass are stored for inspection but grant no local transport
trust. TLS/HTTP enforcement and service authentication are owned by the upcoming
data handlers, not inferred from successful management writes.

Migration 058 keeps encrypted account keys and service settings in a separate
tenant/account-FK table. Each account has two independently rotatable 64-byte keys;
listKeys is the only ordinary management response returning values. Rotation facts
contain key name/time only. State mutations, Activity Log facts and internal events
share a transaction. Credential FK cascades prevent orphan keys on account/group
deletion. Generic inventory imports cannot overwrite operational accounts; inventory-
only account rows remain visible through generic ARM inventory, but do not appear
in operational account lists and return 409 on provider access until explicitly
provisioned through the typed fixture or recreated.

Authenticated local GET/PUT `/_mock/tenants/{tenant}/cloud/storageState` exports and
atomically replaces typed account configuration and service settings. All parents,
names, schemas and tenant ownership are validated before replacement. Exports omit
keys; restore rotates both credentials and invalidates old account event publication.
An empty accounts list resets account state, removing parent-bound credentials;
unchanged account IDs retain scoped grants. Account state has no TTL, so its bounded
prune contributor returns zero; FK cascades perform deletion cleanup. B4 integrates
these typed contributors with combined cloud lifecycle operations.

The original MSAL **1.39.0** confidential client acquired Storage and Key Vault
tokens over real local HTTPS with the instance CA, without any tenant network calls.
Local validation checks signature, issuer, tenant and distinct audiences; ARM rejects
both data tokens. Account integration tests additionally cover rotation, permission
denials, atomic rollback, import isolation, endpoints, networks, lifecycle and upgrade
from 057. The four-ecosystem management matrix remains B4; data signing is C1.

B2 final local gate: **576 passed** across unit, account/provider, OAuth, ARM inventory/mutation/deletion checks; Ruff and offline source verification passed. This count is task-local evidence, not a new all-program release qualification.
