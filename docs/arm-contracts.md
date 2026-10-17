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
