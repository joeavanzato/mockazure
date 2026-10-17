# M5 ARM contract reference

Status: Task1 contract boundary implemented. Public M5 routes and RBAC persistence
are not yet implemented. The predecessor M2 subscription probe remains operational.

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

Authorization2022-04-01 serves the four pinned built-in role definitions and
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
