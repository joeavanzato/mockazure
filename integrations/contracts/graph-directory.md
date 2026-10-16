# Graph directory contract (M3A)

Status: implementation in progress. This document describes the approved target profile; capability endpoints advertise only completed routes. Both versions use separate registrations. API calls are local; links below are documentation references only.

## Authorization matrix

A comma separates alternative permissions. A plus joins permissions that must all be present. Delegated calls also require an active local user; an application token never receives delegated privileges.

| Operation | Application | Delegated | Additional constraint |
| --- | --- | --- | --- |
| List users | User.Read.All, User.ReadWrite.All, Directory.Read.All, Directory.ReadWrite.All | Same, plus User.ReadBasic.All | Basic scope projects only basic fields; local Member callers |
| Read user | Same as list | Same; User.Read for self | User.Read cannot enumerate others |
| Create user | User.Create, User.ReadWrite.All, Directory.ReadWrite.All | Same | Ordinary local Member profile only |
| Update user profile | User.ReadUpdate.All, User.ReadWrite.All, Directory.ReadWrite.All | Same; User.ReadWrite for supported self properties | Delegated updates to others require UserAdministrator or GlobalAdministrator in this bounded role profile |
| Update accountEnabled | User.EnableDisableAccount.All + User.Read.All | Same | Separate from profile authorization; delegated UserAdministrator/GlobalAdministrator; privileged targets unsupported |
| Delete ordinary user | User.ReadWrite.All | User.ReadWrite.All | Delegated UserAdministrator/PrivilegedAuthenticationAdministrator/GlobalAdministrator; privileged targets unsupported |
| List/read groups | GroupMember.Read.All, Group.Read.All, Group.ReadWrite.All, Directory.Read.All, Directory.ReadWrite.All | Same | Property limits apply to membership-only scopes |
| Create static security group | Group.Create, Group.ReadWrite.All, Directory.ReadWrite.All | Group.ReadWrite.All, Directory.ReadWrite.All | Local Member creator; group settings outside static security profile rejected |
| Update/delete static security group | Group.ReadWrite.All | Group.ReadWrite.All | Delegated group owner or supported administrative role |
| Read members/transitiveMembers | GroupMember.ReadBasic.All, GroupMember.Read.All, GroupMember.ReadWrite.All, Group.ReadBasic.All, Group.Read.All, Group.ReadWrite.All, Directory.Read.All | Same | Hidden membership needs Member.Read.Hidden; member-type projection checked separately |
| Read group owners | GroupMember.Read.All, GroupMember.ReadWrite.All, Group.Read.All, Group.ReadWrite.All, Directory.Read.All | Same | v1.0 service-principal owners omitted per documented staged-rollout limitation; beta represents them |
| Add/remove user or security-group member | GroupMember.ReadWrite.All, Group.ReadWrite.All, Directory.ReadWrite.All | Same | Delegated owner or DirectoryWriters/GroupsAdministrator/IdentityGovernanceAdministrator/UserAdministrator/IntuneAdministrator/GlobalAdministrator |
| Read user memberOf/transitiveMemberOf | User.Read.All, Directory.Read.All, Directory.ReadWrite.All, Group.Read.All, GroupMember.Read.All | Same; User.Read for self | Group fields limited unless independently readable; directory roles deferred to C |
| User/group delta | Same resource read requirements | Same resource read requirements | Restricted selected fields and independently checked relationship access |
| Batch GET | Every subrequest's permissions | Every subrequest's permissions | Outer successful batch is not authorization for its contents |

Permission catalog recognition does not promise support for all operations named by a permission. Narrow property-specific scopes outside this matrix remain unsupported. Unsupported permission/role variants are capability limits, not invented blanket consent.

Create-group bindings using Group.Create require permission to read each referenced type: User.Read.All (or Directory.Read.All) for users, Application.Read.All (or Directory.Read.All) for other service principals; the calling application's own SP is allowed. Reject more than 20 combined initial bindings. Preserve documented owner defaults: a delegated nonadmin creator becomes owner when owners are absent; an administrator creating a security group is not automatically owner. An app-only Group.Create without owners creates an anonymous unmodifiable group; do not silently assign an owner.

Sources: [users](https://learn.microsoft.com/en-us/graph/api/user-list?view=graph-rest-1.0), [create user](https://learn.microsoft.com/en-us/graph/api/user-post-users?view=graph-rest-1.0), [update user](https://learn.microsoft.com/en-us/graph/api/user-update?view=graph-rest-1.0), [delete user](https://learn.microsoft.com/en-us/graph/api/user-delete?view=graph-rest-1.0), [groups](https://learn.microsoft.com/en-us/graph/api/group-list?view=graph-rest-1.0), [create group](https://learn.microsoft.com/en-us/graph/api/group-post-groups?view=graph-rest-1.0), [update group](https://learn.microsoft.com/en-us/graph/api/group-update?view=graph-rest-1.0), [delete group](https://learn.microsoft.com/en-us/graph/api/group-delete?view=graph-rest-1.0), [members](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0), [owners](https://learn.microsoft.com/en-us/graph/api/group-list-owners?view=graph-rest-1.0), [add member](https://learn.microsoft.com/en-us/graph/api/group-post-members?view=graph-rest-1.0), [remove member](https://learn.microsoft.com/en-us/graph/api/group-delete-members?view=graph-rest-1.0), [user memberships](https://learn.microsoft.com/en-us/graph/api/user-list-transitivememberof?view=graph-rest-1.0).

## Representations and writes

Users default to id, displayName, userPrincipalName, givenName, surname, mail, businessPhones, jobTitle, mobilePhone, officeLocation and preferredLanguage. The supported selected-only properties are accountEnabled, userType, createdDateTime, department and mailNickname. Basic projection permits id/displayName/UPN/givenName/surname/mail; unavailable unsupported basic properties are not fabricated.

Groups use id/displayName/description/mailNickname/mail/mailEnabled/securityEnabled/groupTypes/visibility/createdDateTime/isAssignableToRole. Newly created local static security groups have mail=null, mailEnabled=false, securityEnabled=true, groupTypes=[] and isAssignableToRole=false. Relationship objects include their real @odata.type. Limited-information entries retain ID/type with other requested fields null.

POST returns 201 with a Graph object. Supported PATCH and DELETE return 204 without JSON. Missing active object returns 404; insufficient permission returns 403 Authorization_RequestDenied; invalid/unknown fields or unsupported query options return Graph-shaped 400 errors. Emulator-only operation gaps use 400 Request_UnsupportedQuery or Request_BadRequest with an explicit supported-profile explanation.

User writes accept only the fields and local-domain/password behavior in the approved plan. Passwords never enter reads, public history, evidence facts or ordinary exports. Soft-deleted users cannot authenticate or appear in active collections. Removal of a membership $ref cannot delete its target object.

## Query profiles

| Resource | Filterable fields/operators | Order | Expand |
| --- | --- | --- | --- |
| users | id,displayName,userPrincipalName,mailNickname,mail,givenName,surname,department: eq/ne/in; displayName/UPN: startswith; accountEnabled: eq/ne; createdDateTime: eq/ge/gt/le/lt | id,displayName,UPN,createdDateTime | None |
| groups | id,displayName,mailNickname,mail,description: eq/ne/in; displayName: startswith; mailEnabled/securityEnabled: eq/ne; createdDateTime: eq/ge/gt/le/lt; groupTypes:any(eq) | id,displayName,createdDateTime | members with inner select; local cap 20, overflow explicit error |
| relationships | Same as returned object type where its Microsoft endpoint permits; limited-information fields cannot be used to infer hidden values | Endpoint-specific declared fields | None |

Filter combinations use and/or/grouping and typed literals. Limits: 8 KiB filter, 256 nodes, depth 16, 100 in-values. Parameterized SQL performs filtering/projection/order and bounded retrieval. Normal pages default to 100 with server cap 999. Generated nextLinks bind query/tenant/version/principal and a committed snapshot; no offset paging.

Advanced combinations obey ConsistencyLevel:eventual and applicable count requirements. Ordinary $count=true without that header is ignored; /$count without it fails. Unknown duplicate options/operators/properties, $skip and $search fail. See [advanced directory queries](https://learn.microsoft.com/en-us/graph/aad-advanced-queries). The exact property/operator allowlist in code may be narrower when the endpoint documentation does not support a table combination; register and test those exceptions explicitly.

User/group delta accepts select and state links only in A. Unsupported top/orderby/filter/expand/search fail. A round has a fixed upper revision. Selected-only property changes trigger updates; tombstones use @removed.reason=deleted. Group members@delta is included by default or when selected; large changes use repeated group records in bounded pages. Empty completed rounds return deltaLink. Expired seven-day state returns 410 syncStateNotFound plus a local resync Location; ordinary cursors expire after one hour. These local defaults and snapshot guarantees are documented implementation choices. See [delta overview](https://learn.microsoft.com/en-us/graph/delta-query-overview) and [group delta](https://learn.microsoft.com/en-us/graph/delta-query-groups).

## Evidence and qualification boundary

M3A commits a sanitized durable evidence outbox with each mutation. Projection into directoryAudits belongs to M3B. Original Hawk/MES collection and policy-enforcement checks remain B/C. A's completion report must prove actual SDK results, restart/replica continuity, tenant isolation, 5,000-user paging and 429 handling. No scenario is required for normal operation.

Relationship collections support select/top and snapshot-bound opaque continuations. Expanded members have an explicit 20-edge limit and share the parent collection snapshot. Group lifecycle, membership and ordinary user changes produce immutable public differences/edge facts for the pending M3B projector; they do not yet produce directoryAudits.

Deletion is permanent in this local profile: user/group tombstones use reason=deleted and no restore operation exists. Microsoft's restorable user deletion uses reason=changed. Group delta omits implicit member removals caused by deletion of the member, matching the documented Graph limitation. A cleaned-up unavailable delta handle returns 410 with a full resync Location; retained expired state preserves the original select.

The request budget is a local fixed-window profile: graph_directory_request_limit=600 and graph_directory_window_seconds=60, keyed by tenant/client/users-or-groups and shared across API versions. Batch GET items each consume budget. Body limit is 1 MiB; responses are capped at 8 MiB per item and 32 MiB per batch. Retry controls are POST /_mock/tenants/{id}/graph/faults with client_id, resource=users|groups, kind=next_read_429 and optional retry_after=1..300. Only one pending fault per scope is allowed. See https://learn.microsoft.com/en-us/graph/json-batching and https://learn.microsoft.com/en-us/graph/throttling.

## Public directory datasets
POST /_mock/tenants/{id}/directory/import accepts schema_version=1, users, groups, relationships and provenance. It is create-only: 10 MiB and 10,000 combined user/group objects, no duplicate IDs/UPNs or cycles, and all relationship references must be included in the dataset. Provenance accepts source/description/seed/generator scalar metadata. Users have no imported password verifier and cannot authenticate.
GET /_mock/tenants/{id}/directory/export streams a fixed revision in 500-record chunks. Object IDs remain globally unique in an instance; restoration with the same IDs requires an empty destination for those identities. Service-principal owners require the later C dataset extension; export currently returns UnsupportedDatasetReference instead of silently losing them.
These are public fixtures, not identity or authorization backups. Grants, roles, sessions, credentials and internal operational flags are omitted. Existing tenant.export jobs retain their metadata-only schema.
CLI commands: mockazure directory import --tenant-id UUID --file PATH --config PATH; mockazure directory export --tenant-id UUID --output PATH --config PATH; mockazure directory cleanup --tenant-id UUID --config PATH. Output never overwrites an existing file. Cleanup is also POST /_mock/tenants/{id}/directory/cleanup and retains history required by unexpired state links.

Generated SDK writes may include @odata.type exactly matching #microsoft.graph.user
or #microsoft.graph.group for that route; it is validated and removed before the
domain mutation. Mismatched type annotations fail. Delta accepts delta,
microsoft.graph.delta and their parenthesized function-call aliases.
