# MockAzure M3A: Stateful Directory, Query and Delta Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [x]) syntax for tracking.

**Goal:** Make real Graph clients read and mutate a persistent local user/group directory, including reliable paging and incremental synchronization.

**Architecture:** Extend the existing identity rows with Graph profile state and add tenant-constrained groups, relationships and public revision history. Typed query contracts compile to bounded PostgreSQL queries; persisted opaque continuation handles carry immutable snapshot/query context. Directory mutations commit state, history and an evidence outbox atomically.

**Tech Stack:** Existing Python >=3.12, FastAPI, SQLAlchemy 2, psycopg 3, Alembic, PostgreSQL 18, cryptography, Pydantic, HTTPX, Typer and pytest. Preserve MSAL 1.39.0 and msgraph-sdk 1.64.0; add Microsoft.Graph.Users and Microsoft.Graph.Groups 2.35.1 to the client manifest with verified dependencies and hashes.

**Spec:** [Approved overall design](../../../../superpowers/specs/2026-10-16-mockazure-design.md) and [proposed M3 delivery map](../specs/2026-10-16-mockazure-stateful-graph.md).
**Status:** Completed 2026-10-16; all nine Native implementation tasks and independent review fixes verified. 197 unit/integration tests, one acceptance gate and 14 qualification checks passed. See the execution and rulings records for evidence and documented adjustments.
**Execution preference:** Native, already selected. Main agent implements; one independent whole-change review before delivery.
**Baseline:** Merged M2 tree a3074f69b16f7abcc76dff93de518c722c159348. M2 integration PR #3 remains the prerequisite for merging. Recorded delivery adjustment: publish M3A as a draft targeting main while #3 is open; its comparison includes M2 until that integration lands. Do not merge on the user's behalf.

## Global Constraints

- All new project files, research downloads, generated credentials, data, exports and test output belong under D:\projects\mockazure.
- Never edit, generate files, install dependencies, run tests or initialize tools inside E:\projects\vtw. Do not access VTW again.
- Runtime and integration tests never contact a real Microsoft tenant. There is no upstream fallback or transparent forwarding to Microsoft.
- Official documentation and public client source are acceptable research inputs. Dependency acquisition is a separate preparation step; compatibility runs operate with external service access blocked.
- PostgreSQL is the system of record; there is no SQLite alternative.
- The native Python/CLI installation has the same functionality and requires PostgreSQL.
- Default published ports bind locally. Do not change Windows trust stores, hosts, DNS, global profiles, Git configuration or firewall rules.
- Ordinary shareable dataset exports omit credentials, private keys and session secrets.
- Licence: Apache-2.0. Commit coherent changes frequently; never force-push over unknown history.
- Preserve the running M1 and M2 databases, runtime files and images. M3 uses .runtime/m3, image mockazure:graph, Compose project mockazure-m3 and available loopback ports starting at HTTPS 48443 / PostgreSQL 55435.
- Existing Exchange and ARM connection contracts remain regression gates, not new feature scope.
- Unknown routes, fields, query operators and unsupported writes must fail explicitly. Do not accept inputs that have no effect.

## Review Focus

1. An update commits after a later allocated revision: no omitted delta event or inconsistent paging watermark (Tasks 1, 4, 6).
2. Permissions change or a continuation is replayed by another tenant/client/user: no snapshot or property leakage (Tasks 2, 4, 6).
3. A user is disabled/deleted through Graph while local sessions exist: subsequent authentication and protected requests follow the shared identity state (Task 5).
4. Mixed-member groups, cycles, large expansions and limited member permissions: bounded traversal with honest object types and no hidden data exposure (Tasks 2, 3, 5).
5. Apostrophes, Unicode, nulls, malformed/deep filters, duplicate query options and hostile $ref URLs: no parser ambiguity, injection, outbound fetch or unbounded work (Tasks 3, 5, 7).

## Deliverable contract

The following routes exist in v1.0 and beta with explicitly registered contracts. Beta initially exposes the same supported user/group field subset; it is not a blanket alias for future v1.0 additions.

| Resource | Supported routes and methods |
| --- | --- |
| Users | GET /users; GET /users/{id-or-UPN}; POST /users; PATCH and DELETE /users/{id-or-UPN} |
| Self | Existing GET /me, using the same user projection and preserving M2's supported behavior |
| Groups | GET/POST /groups; GET/PATCH/DELETE /groups/{id} |
| Relationships | GET /groups/{id}/members, /transitiveMembers, /owners; GET /users/{id-or-UPN}/memberOf and /transitiveMemberOf |
| Membership writes | POST /groups/{id}/members/$ref; DELETE /groups/{id}/members/{id}/$ref |
| Synchronization | GET /users/delta and /groups/delta, plus the generated-SDK microsoft.graph.delta path spelling |
| Batching | POST /$batch for independent GET requests to supported directory routes; maximum 20 |
| Organization | Existing GET /organization remains compatible |

Group membership writes accept local users and static security groups. Group owners may be local users or existing service principals; create-time bindings are validated and permission-checked. Device/SP membership mutation and standalone owner writes remain unsupported in A; C extends relationships with its resource contracts.

User reads support id, displayName, userPrincipalName, givenName, surname, mail, mailNickname, accountEnabled, userType, createdDateTime, jobTitle, department, officeLocation, mobilePhone, businessPhones and preferredLanguage. Respect documented default-vs-selected properties. Credential verifiers and passwordProfile never appear in reads/history/export. Groups expose id, displayName, description, mailNickname, mail, mailEnabled, securityEnabled, groupTypes, visibility, createdDateTime and isAssignableToRole. Unsupported properties fail selection explicitly.

User creates require displayName, userPrincipalName, mailNickname, accountEnabled and passwordProfile.password. Only local verified tenant domains and ordinary Member users are writable. forceChangePasswordNextSignIn must explicitly be false; true returns a documented unsupported-operation error. Profile PATCH supports displayName, givenName, surname, mailNickname, jobTitle, department, officeLocation and preferredLanguage; accountEnabled follows its separate sensitive-write permission rule. Do not allow editing derived mail/userType/creation values or resetting passwords through this bounded profile.

Group writes support displayName, description and mailNickname with groupTypes=[], mailEnabled=false, securityEnabled=true and isAssignableToRole=false. Creation may bind owners/members with the documented combined limit of 20. Reject dynamic, Microsoft 365 and role-assignable group writes. Ordinary user and security-group deletion returns 204; subsequent active reads return 404 and delta exposes removal. Privileged user targets are explicitly unsupported after normal authorization, until C supplies hierarchy-aware contracts. No deletedItems restore endpoint is claimed.

### Query and authorization rules

- Each route records allowed fields, operators, projection, defaults, permission alternatives, delegated role/ownership rules and version in integrations/contracts/graph-directory.md. This matrix is a test input, not prose asserting that every Graph operation supports every query.
- Core filters: eq/ne on supported scalar properties; booleans/null; and/or/parentheses; in on permitted string/ID fields; startswith on displayName/UPN; datetime comparisons on createdDateTime; any on declared collections. Directory relationship any and advanced combinations require the documented headers/options.
- Parser limits in this local profile: 8 KiB filter text, 256 AST nodes, depth 16, in lists of 100 values; reject overflow with a Graph-shaped 400. Only declared fields/operators compile to parameterized SQL.
- Ordinary collections: $select, $top, supported $orderby, $count and generated $skiptoken. Default page 100, cap 999; stable ordering uses id as final tie-breaker. No $skip/$search in A.
- Advanced directory queries require ConsistencyLevel:eventual and applicable $count=true. Follow the documented distinction: ordinary $count=true without the header is ignored; /$count without its required header fails. Implement /users/$count and /groups/$count in Task 3.
- Only group-members $expand is supported in A, with $select inside the expansion. Bounded expansion is an explicit local profile: at most 20 members; if more exist, fail explicitly and direct callers to the pageable members route rather than silently truncate. Advanced-query combinations that disallow expand fail.
- Limited-information relationship objects retain id and @odata.type with other requested properties null when the caller can enumerate the relationship but cannot read the member type. Do not silently drop members.
- Preserve specific operation permissions. ReadBasic access does not imply full user profile. User.Read alone does not list the tenant. accountEnabled requires the documented permission combination or an explicitly documented higher-privilege equivalent; an OR-set implementation must not turn an AND requirement into either permission alone.
- Evaluate current delegated user state and applicable roles on every request. Current permissions, tenant and resource audience are checked again for page/delta/batch requests. Relationship/field access is never inferred from a successful parent read.

### Persistence and continuation rules

The shared directory service accepts a caller context and validated command. It owns no outer commit: callers commit one transaction covering the identity/profile mutation, relationship changes, revision history and public evidence outbox. Identity administrative writes use the same mutation boundary so Graph/delta sees them too.

A tenant revision row is locked until commit; public object/relationship versions have valid-from/valid-to revisions. Read the committed watermark at the first page, and query history as of that watermark with keyset paging. Do not hold a database transaction between HTTP requests. History contains only public/projectable state; outbox facts omit passwords, credential values and bearer tokens.

Continuation tokens are random 32-byte opaque handles. Store only their SHA-256 hashes in PostgreSQL with immutable server-side context: tenant, API version, resource, normalized query, snapshot/round bounds, last key, subject/client/token kind, permission fingerprint, creation/expiry and tenant-reset generation. Clients cannot edit embedded cursor state because none is client-carried. Reauthorize before lookup/use; reject context mismatch. Reusing a valid link is deterministic; tokens are not single-use.

Ordinary continuations expire after one hour; delta state after seven days. These configurable local-profile defaults are not claims about all Microsoft endpoints. Expired delta returns 410 with error.code=syncStateNotFound and a Location header pointing to the same local delta resource with an empty $deltatoken, preserving selected fields. An empty $deltatoken starts a fresh round. Keep versions needed by every unexpired cursor and delta window; cleanup must retain the boundary version that precedes a live watermark. Tenant deletion invalidates its tokens without affecting other tenants. Persist a generation UUID for future reset support; A tests generation invalidation directly but adds no destructive public reset endpoint.

Delta uses a dedicated parser: $select and opaque state links, not $top/$orderby/$expand/$search. The A profile also rejects delta $filter. Initial round enumerates snapshot state; completed rounds return @odata.deltaLink. Incremental rounds use fixed lower/upper revisions, return selected-field changes and deletion tombstones, and defer later commits to the next round. Group members@delta is included when members is selected or no select is supplied. Chunk large membership changes into bounded repeated group records; do not build an unbounded group array.

## File and interface map

| Area | Files / responsibility |
| --- | --- |
| Persistence | directory/models.py, directory/types.py, directory/history.py, directory/repository.py; new Alembic revisions 009 onward |
| Shared writes | directory/mutations.py and directory/references.py; identity/repository.py delegates relevant admin writes |
| Query | providers/graph/query/{ast,parser,contracts,compiler}.py; explicit property/operator contracts and SQL plans |
| Reads | providers/graph/directory.py, serialization.py, permissions.py; reuse principal/permitted and GraphError |
| State links / delta | providers/graph/continuations.py, delta.py; persisted handles, immutable rounds and links |
| Cross-request limits | providers/graph/throttle.py and batch.py; persistent quotas and internal subrequest execution |
| Import/export | directory/datasets.py, admin/directory_routes.py, commands/directory.py; separate directory dataset format preserves existing tenant exports |
| Qualification | tools/prepare_graph.py, tools/verify_graph.py, compose.m3.yaml; integrations/python/graph_directory.py and powershell/Test-GraphDirectory.ps1 |

Source paths in that map are relative to src/mockazure unless prefixed with tools, integrations or compose. Add __init__.py only where packaging requires it. Keep provider/domain modules separate; do not enlarge probes.py into the entire Graph server.

## Task 1: Tenant-safe state, history and upgrade

**Files:** Create src/mockazure/directory/{models,types,history,repository}.py; src/mockazure/db/alembic/versions/009_graph_directory.py; tests/integration/test_graph_directory_storage.py; integrations/contracts/graph-directory.md. Modify src/mockazure/identity/models.py, src/mockazure/identity/repository.py and src/mockazure/db/alembic/env.py.

**Interfaces:** DirectoryKind = Literal["user","group"]; DirectoryObject(kind: DirectoryKind, id: UUID, tenant_id: UUID, properties: dict); DirectoryChange(revision: int, kind: DirectoryKind, object_id: UUID, changed_fields: frozenset[str], removed: bool). Produce async reserve_revision(session: AsyncSession, tenant_id: UUID) -> int and async record_change(session: AsyncSession, change: DirectoryChange, before: DirectoryObject | None, after: DirectoryObject | None, facts: dict) -> None. Public facts have an explicit allowlist and schema version; never accept raw request bodies.

- [x] Write test_upgrade_and_tenant_constraints: migrate a populated M2 database, assert existing user/client IDs and credential validation survive; cross-tenant membership/owner inserts raise IntegrityError; foreign-tenant objects are absent from reads.
- [x] Write test_commit_order_and_atomic_history: hold transaction A's revision lock, assert B cannot publish a later watermark first; rollback A leaves no history/outbox/object change; successful write creates exactly one operation outbox row. Assert serialized history/outbox has no verifier/password/token fields.
Core assertions: `assert after_user.id == before_user.id`; `assert not later_commit.done()` while A holds the lock; `assert (object_count, history_count, outbox_count) == before_counts` after rollback.

- [x] Run .venv/Scripts/python.exe -m pytest tests/integration/test_graph_directory_storage.py -q; expect missing schema/interfaces or failed invariants.
- [x] Implement typed profile columns plus JSONB for declared extras; preserve User identity IDs and add deleted_at. Add groups, tenant-constrained relationships, revision/history/outbox and continuation-state tables, indexes for tenant/kind/revision/object and relationship traversal. Backfill existing public users at a committed baseline revision. Connect existing administrative user create/enable updates to record_change immediately, so subsequent read/query tasks do not lose post-migration administrative writes. Task 5 later consolidates these hooks into the shared mutation service.
- [x] Populate the contract matrix from official endpoint docs linked below: exact app/delegated alternatives, supported roles, field-level rules and resource/default property shapes. Document local limits beside Microsoft behavior; resolve discrepancies against the endpoint-specific source, not broad permission names.
- [x] Run the storage tests and tests/integration/test_identity_upgrade.py; expect all pass. Commit as feat(graph): add tenant-scoped directory history and upgrade.

## Task 2: Resource reads and operation-specific authorization

**Files:** Create src/mockazure/providers/graph/{directory,serialization,permissions}.py; tests/integration/test_graph_directory_reads.py. Modify src/mockazure/authorization/{catalog,evaluator}.py, src/mockazure/providers/graph/probes.py and src/mockazure/admin/app.py.

**Interfaces:** Produce GraphOperation(version: str, resource: str, method: str, fields: frozenset[str]); async authorize_directory(session: AsyncSession, context: PrincipalContext, operation: GraphOperation, target_id: UUID | None) -> None; serialize_object(obj: DirectoryObject, fields: tuple[str,...] | None, readable_fields: frozenset[str]) -> dict. Consume Task 1 objects and existing principal()/GraphError. Model permission alternatives as OR of required sets, with all members of a set required.

- [x] Write test_directory_reads_share_identity: a user created through existing admin APIs is returned by ID and encoded UPN, /me matches it, organization remains compatible and missing/wrong-tenant IDs return the same 404.
- [x] Write test_permission_matrix using every contract row: read-basic yields only allowed data, User.Read cannot list users, wrong audience is 401 and absent scope is 403. For relationship reads without member-type permission, assert ID/type retained and protected requested properties null.
- [x] Write test_sensitive_permission_conjunction: either half of the accountEnabled requirement is denied, both grant authorization for ordinary targets; role-restricted delegated operations still require their role.
Core assertions: `assert list_as_user_read.status_code == 403`; `assert wrong_audience.status_code == 401`; `assert limited_member['displayName'] is None`; `assert half_permission.status_code == 403`.

- [x] Run .venv/Scripts/python.exe -m pytest tests/integration/test_graph_directory_reads.py -q; expect unsupported routes/permissions.
- [x] Implement read handlers and serializers with explicit v1.0/beta registrations, existing error/request-ID conventions and no secret model serialization. Keep read permissions separate from mutation target checks; extend the evaluator without weakening M2 callers.
- [x] Run this test file plus tests/integration/test_identity_graph.py and tests/integration/test_identity_authorization.py; expect pass. Commit as feat(graph): expose permission-checked directory reads.

## Task 3: Typed query execution and bounded relationships
**Files:** Create src/mockazure/providers/graph/query/{__init__,ast,parser,contracts,compiler}.py; tests/unit/test_graph_query_parser.py; tests/integration/test_graph_query.py. Modify directory/repository.py and providers/graph/directory.py.

**Interfaces:** Produce QuerySpec(filter: FilterNode | None, select: tuple[str,...] | None, order: tuple[OrderTerm,...], top: int | None, count: bool, expand: tuple[ExpandSpec,...]); parse_query(items: list[tuple[str,str]], contract: QueryContract, consistency: str | None) -> QuerySpec; compile_filter(node: FilterNode, contract: QueryContract) -> ColumnElement[bool]. AST nodes are Field/Literal/Compare/Boolean/In/Any/StartsWith; contracts bind fields to typed SQL columns/expressions. Produce async read_page(session, tenant_id: UUID, kind: DirectoryKind, query: QuerySpec, snapshot: int, after: tuple | None, limit: int) -> DirectoryPage(items: list[DirectoryObject], last_key: tuple | None, has_more: bool, total: int | None).

- [x] Write test_typed_filters_and_bounds: eq/ne/null/booleans/time ranges/in/any and apostrophe escaping produce expected IDs; Unicode survives; malicious field paths, duplicate options, depth 17, >256 nodes and >100 in-values yield 400. SQL compilation contains bound parameters, not interpolated literals.
- [x] Write test_advanced_query_contract: filter+order requires the contract's eventual/count combination; ordinary count without header is omitted; /$count without header fails; authorized count matches the filtered collection.
- [x] Write test_relationship_bounds: a nested group graph with a cycle terminates, deduplicates transitive IDs and remains tenant-scoped; 21-member expansion fails explicitly; pageable members returns all 21. Unsupported collection operators and nested expansions fail.
Core assertions: `assert depth_17.status_code == 400`; `assert filtered_ids == expected_ids`; `assert len(transitive_ids) == len(set(transitive_ids))`; `assert '@odata.count' not in ordinary_without_header.json()`.

- [x] Run .venv/Scripts/python.exe -m pytest tests/unit/test_graph_query_parser.py tests/integration/test_graph_query.py -q; expect missing parser/compiler.
- [x] Implement recursive-descent parsing and parameterized SQL compilation with resource-specific allowlists. Apply filtering/projection/order/count/paging in SQL; recursive CTE traversal must be cycle-safe. Never fetch an entire tenant to filter/sort in Python.
- [x] Run both test files; inspect EXPLAIN for a 5,000-user selected/filter query and verify bounded result fetches. Commit as feat(graph): compile bounded directory queries to PostgreSQL.

## Task 4: Stable continuation pages across replicas

**Files:** Create src/mockazure/providers/graph/continuations.py; tests/integration/test_graph_paging.py. Modify directory/history.py and providers/graph/directory.py.

**Interfaces:** Produce CursorBinding(tenant_id: UUID, generation: UUID, version: str, resource: str, client_id: UUID, subject_id: UUID | None, token_kind: str, permissions_hash: str); CursorState(binding: CursorBinding, query: QuerySpec, lower_revision: int | None, upper_revision: int, last_key: tuple | None, kind: Literal["page","delta"], expires_at: datetime); async issue_cursor(session, state: CursorState) -> str and async resolve_cursor(session, token: str, binding: CursorBinding, now: datetime) -> CursorState. Resolve rejects mismatches and expired/unknown handles with route-appropriate GraphError.

- [x] Write test_paging_snapshot_under_mutation: traverse 5,000 users while another connection inserts, renames and deletes users; assert returned IDs/order/properties match the initial snapshot exactly with no duplicate/missing IDs, and a fresh query sees changes.
- [x] Write test_cursor_restart_and_binding: issue on API A, resume on B and after restart; tampered token, changed query, other tenant/client/user/version and changed permission fingerprint fail. Repeating the same valid link returns the same page.
- [x] Write test_history_cleanup_preserves_live_boundary: a live snapshot older than an object's latest version remains readable after cleanup; expired handle fails and another tenant's cursor is unaffected by generation invalidation.
Core assertions: `assert collected_ids == snapshot_ids`; `assert len(set(collected_ids)) == 5000`; `assert page_from_replica_b == page_from_replica_a`; `assert forged.status_code == 400`.

- [x] Run .venv/Scripts/python.exe -m pytest tests/integration/test_graph_paging.py -q; expect missing continuations or snapshot failures.
- [x] Implement hashed opaque handles and configured-profile nextLinks. Store immutable cursor context in PostgreSQL; make cap and token-size validation bounded. Reauthorize before reading snapshot data and retain history required by live handles.
- [x] Run paging tests; expect pass. Commit as feat(graph): persist snapshot-bound continuation pages.

## Task 5: Shared lifecycle and relationship mutations

**Files:** Create src/mockazure/directory/{mutations,references}.py; tests/integration/test_graph_mutations.py. Modify identity/repository.py, oauth/authorization.py, oauth/device.py, oauth/sessions.py, identity/tokens.py, providers/graph/directory.py and authorization/evaluator.py only where active-user checks/shared writes require it.

**Interfaces:** Produce DirectoryCommand(kind: DirectoryKind, action: Literal["create","update","delete","add_member","remove_member"], target_id: UUID | None, values: dict); async mutate_directory(session: AsyncSession, tenant_id: UUID, command: DirectoryCommand, actor: PrincipalContext | AdminActor, now: datetime) -> MutationResult(object: DirectoryObject | None, revision: int, outbox_id: UUID). AdminActor is a distinct trusted internal type, never constructible from a provider request. Produce parse_local_reference(value: str, allowed_graph_origins: frozenset[str], tenant_id: UUID) -> UUID; it parses/validates but never fetches a URL.

- [x] Write test_user_lifecycle_auth_effects: authorized create returns 201 and local login succeeds; profile PATCH returns 204 and changes both reads and history; disable/delete blocks subsequent login/refresh and protected use according to existing live-state enforcement. No password appears in response/outbox/export.
- [x] Write test_mutation_atomicity_and_references: invalid password/UPN domain, cross-tenant member, malformed/external $ref and duplicate membership fail with zero partial object/history/outbox changes; concurrent duplicate membership has one relationship and one success. Removing /$ref removes only membership, never the referenced user.
- [x] Write test_bounded_write_profile: force-change-password=true, privileged user target, dynamic/role-assignable/M365 groups and unknown fields fail explicitly; ordinary user deletion yields 204 and subsequent read 404. Validate combined initial owner/member cap and type-specific read permission requirements.
Core assertions: `assert created.status_code == 201`; `assert patched.status_code == 204`; `assert deleted.status_code == 204`; `assert read_deleted.status_code == 404`; `assert user_still_exists_after_remove_ref is True`.

- [x] Run .venv/Scripts/python.exe -m pytest tests/integration/test_graph_mutations.py -q; expect unsupported writes or missing shared-state behavior.
- [x] Implement command validation, operation/property/target authorization and transactional mutation/history/outbox writes. Retain soft-deleted users and membership history; exclude them from active authentication/reads. Preserve referential integrity and documented group-owner creation rules. Route admin identity user creates/updates through the same history-producing service without duplicating revisions.
- [x] Run mutation tests and existing identity session/token/admin tests; expect pass. Commit as feat(graph): persist authorized directory mutations and evidence outbox.

## Task 6: User/group delta and resynchronization

**Files:** Create src/mockazure/providers/graph/delta.py; tests/integration/test_graph_delta.py. Modify query/contracts.py, directory/history.py and providers/graph/directory.py.

**Interfaces:** Produce DeltaRequest(select: tuple[str,...] | None, token: str | None); parse_delta(items: list[tuple[str,str]]) -> DeltaRequest; async read_delta(session, context: PrincipalContext, binding: CursorBinding, request: DeltaRequest, now: datetime) -> DeltaPage(value: list[dict], next_link: str | None, delta_link: str | None). Consume Task 4 cursors; exactly one link type is returned per successful page.

- [x] Write test_delta_rounds_and_selection: initial round covers all active IDs; a later round returns selected-property updates, creation and @removed tombstones, excludes updates only to unselected fields, and does not lose a commit occurring during enumeration. An unchanged round returns an empty value and a valid deltaLink.
- [x] Write test_group_membership_delta: add/remove members appears as members@delta with documented removal shape, is absent when unselected, and a 1,001-change group is delivered across bounded repeated group records without lost references.
- [x] Write test_delta_contract_and_expiry: both route spellings work; top/orderby/expand/filter are rejected; malformed/cross-tenant/version tokens fail; expiry yields 410, error.code=syncStateNotFound and a local Location containing empty $deltatoken; reinitialize and obtain complete current state.
Core assertions: `assert unchanged.json()['value'] == []`; `assert '@odata.deltaLink' in unchanged.json()`; `assert expired.status_code == 410`; `assert expired.json()['error']['code'] == 'syncStateNotFound'`.

- [x] Run .venv/Scripts/python.exe -m pytest tests/integration/test_graph_delta.py -q; expect unsupported delta routes.
- [x] Implement fixed upper/lower revision rounds, selected-field change detection, membership chunks and bounded tombstones. Record user/group deletion as @removed with reason=deleted, and membership removal as the member ID with @removed.reason=deleted. Confirm these fixtures against the linked directory delta docs; do not borrow mailbox/drive-specific behavior.
- [x] Run delta and paging tests; expect pass. Commit as feat(graph): implement persistent user and group delta.

## Task 7: Read batching, shared throttling and controlled retry faults

**Files:** Create src/mockazure/providers/graph/{batch,throttle}.py; src/mockazure/db/alembic/versions/010_graph_limits.py; tests/integration/test_graph_batch_limits.py. Modify admin/app.py and config.py.

**Interfaces:** Produce async consume_budget(session, tenant_id: UUID, client_id: UUID, resource: str, now: datetime, limit: int, window_seconds: int) -> int | None (None permits, integer Retry-After denies). Produce async dispatch_batch(request: Request, items: list[dict], context: PrincipalContext) -> list[dict]. Reuse authenticated provider execution internally without outbound HTTP or trusting arbitrary batch URLs.

- [x] Write test_batch_contract: up to 20 independent GETs yield individually authorized status/body/headers keyed by ID; bad individual requests do not gain privileges; 21 items, duplicate IDs, dependsOn, non-GET and absolute/foreign URLs are explicitly rejected.
- [x] Write test_shared_limit_and_retry: two replicas share a per-tenant/client/resource budget; request beyond configured test limit returns 429 and positive Retry-After, retry after expiry succeeds; another tenant remains unaffected.
- [x] Write test_fault_scope_and_no_partial_write: an opt-in admin-created next-read 429 fault is tenant/client/resource scoped, consumed atomically once and survives restart; no default fault exists and no provider caller can install one.
Core assertions: `assert over_limit.status_code == 429`; `assert int(over_limit.headers['Retry-After']) > 0`; `assert after_window.status_code == 200`; `assert len(batch.json()['responses']) == 20`.

- [x] Run .venv/Scripts/python.exe -m pytest tests/integration/test_graph_batch_limits.py -q; expect missing batch/limits.
- [x] Implement PostgreSQL shared fixed-window budgets; default 600 directory requests/minute per tenant/client, configurable and documented as a local profile. Batch subrequests each consume budget and can individually return 429. Expose fault controls through the authenticated admin plane only. Expose POST /_mock/tenants/{id}/graph/faults with client_id, resource and kind=next_read_429, bounded to one outstanding fault per tuple. Add a sanitized Graph JSON body parser with a 1 MiB request limit before batch dispatch; directory import has its separate 10 MiB administrative limit.
- [x] Run this test file and boundary regressions; expect pass. Commit as feat(graph): add bounded batch and shared throttling.

## Task 8: Safe directory datasets and persistence tooling

**Files:** Create src/mockazure/directory/datasets.py, src/mockazure/admin/directory_routes.py, src/mockazure/commands/directory.py, tests/integration/test_graph_datasets.py. Modify admin/app.py and cli.py. Keep the existing tenant.export job schema and execution unchanged; directory export has a separate streamed endpoint.

**Interfaces:** Produce DirectoryDataset(schema_version: Literal[1], users: list[PublicUser], groups: list[PublicGroup], relationships: list[PublicRelationship], provenance: dict); async import_directory(session, tenant_id: UUID, dataset: DirectoryDataset, mode: Literal["create"]) -> ImportResult(objects: int, relationships: int, revision: int). PublicUser/Profile types use the Task 1 public field allowlist and never contain credentials. An imported user without separate administrative credential provisioning cannot authenticate.

- [x] Write test_directory_dataset_roundtrip: export/import a bounded fixture, assert IDs/public fields/relationships/provenance match, no secrets and no login for imported users lacking credentials. Existing M1 tenant-export jobs remain readable and executable.
- [x] Write test_import_atomicity_and_limits: invalid foreign references, duplicate IDs, unsupported schema version, unknown fields and oversize body fail without partial import; ordinary shareable data cannot restore sessions/keys. An imported change appears in Graph and delta with import provenance.
Core assertions: `assert roundtrip_public_ids == original_public_ids`; `assert imported_user_login.status_code == 400`; `assert counts_after_invalid_import == counts_before_import`; `assert secret_field_names.isdisjoint(exported_keys)`.

- [x] Run .venv/Scripts/python.exe -m pytest tests/integration/test_graph_datasets.py -q; expect missing import/export fields.
- [x] Implement authenticated POST /_mock/tenants/{id}/directory/import and GET /_mock/tenants/{id}/directory/export and CLI mockazure directory import --tenant-id UUID --file PATH. Import is create-only with a 10 MiB/10,000-object local limit and atomic validation; no silent overwrite. Provide CLI mockazure directory export --tenant-id UUID --output PATH. Stream the versioned directory dataset at a fixed revision through bounded queries without loading all tenant objects at once. Keep the existing M1 tenant-only export endpoint/job backward compatible and document the distinct formats.
- [x] Run dataset tests and tests/integration/test_exports.py tests/integration/test_jobs.py; expect pass. Commit as feat(graph): add safe directory datasets and exports.

## Task 9: Real clients, isolated deployment and delivery evidence

**Files:** Create compose.m3.yaml, tools/prepare_graph.py, tools/verify_graph.py, integrations/python/graph_directory.py, integrations/powershell/Test-GraphDirectory.ps1, tests/acceptance/test_graph_distribution.py and docs/archive/m1-m6/graph-directory-execution.md. Modify integrations/manifest.json, tools/prepare_clients.py if new dependency validation requires it, docs/{capabilities,compatibility}.md and README.md.

**Interfaces:** prepare_graph.py builds only project-local M3 configuration/fixtures and prints no secrets. verify_graph.py --report work/graph-directory-report.json qualifies the exact source/package fingerprint, returns nonzero on any required gate and emits a sanitized versioned report with per-probe results. Client scripts consume the existing endpoint/auth configuration schema plus explicit fixture IDs; no real-service defaults.

- [x] Write acceptance expectations before the verifier: actual Python and PowerShell SDKs must create a user/group, enumerate pages, mutate membership/profile, observe delta and delete; returned IDs/fields must equal fixture expectations, not just nonempty output. Both app-only and delegated modes have success and denial probes.
- [x] Acquire Users/Groups 2.35.1 and declared dependencies in the separate preparation step, pin archive hashes and verify complete extracted trees using existing manifest checks. Preserve M2 pins. Run preparation integrity tests; do not download at compatibility-test time.
- [x] Run the new acceptance gates against a disposable M3 test instance before adding verifier/client support; expect missing verification interfaces. Do not test new writes against preserved M2. Record the RED result and its actual cause.
- [x] Implement M3 native and Docker setup on distinct available ports/volumes, isolated client networking and the verifier. Include 5,000-user paging with concurrent mutations, two API replicas, API/PostgreSQL restart, continuation/delta reuse, rejected cross-tenant references, 429 recovery and no runtime Internet reachability. Exercise clean wheel install as well as source/container execution. Use a dedicated new database; never upgrade preserved M1/M2 databases.
- [x] Run .venv/Scripts/python.exe -m pytest tests/unit tests/integration -q, then .venv/Scripts/python.exe -m pytest tests/acceptance/test_graph_distribution.py -q, .venv/Scripts/python.exe tools/verify_graph.py --report work/graph-directory-report.json, .venv/Scripts/ruff.exe check ., .venv/Scripts/python.exe tools/check_package.py and git diff --check. Require all exit 0; record exact commands, profile, counts, durations, fingerprints and limitations. Do not run pytest suites concurrently.
- [x] Record that A's evidence outbox is pending B, collector evidence gates remain B, and C security surfaces are unimplemented. Add one independent whole-change review under Native, resolve findings, rerun affected checks and regenerate qualification after runtime changes.
- [x] Commit as test(graph): qualify persistent directory SDK workflows. Push and create an implementation PR targeting main; publish as a draft with dependency #3 while the M2 integration prerequisite remains open, per the recorded delivery adjustment; attach it to this chat. No automatic merge.

## References and self-review

Primary contracts: [list users](https://learn.microsoft.com/en-us/graph/api/user-list?view=graph-rest-1.0), [create user](https://learn.microsoft.com/en-us/graph/api/user-post-users?view=graph-rest-1.0), [update user](https://learn.microsoft.com/en-us/graph/api/user-update?view=graph-rest-1.0), [delete user](https://learn.microsoft.com/en-us/graph/api/user-delete?view=graph-rest-1.0), [list groups](https://learn.microsoft.com/en-us/graph/api/group-list?view=graph-rest-1.0), [create group](https://learn.microsoft.com/en-us/graph/api/group-post-groups?view=graph-rest-1.0), [group members](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0), [advanced directory queries](https://learn.microsoft.com/en-us/graph/aad-advanced-queries), [delta overview](https://learn.microsoft.com/en-us/graph/delta-query-overview), [group delta](https://learn.microsoft.com/en-us/graph/api/group-delta?view=graph-rest-1.0).

Self-review before handoff: each M3 requirement is assigned in the delivery map; A does not claim B/C completion. The five Review Focus cases have named owning tests. Public query/history types are shared consistently. No implementation task uses VTW, real tenants, host-wide changes or canned SDK/collector replacements. Endpoint-specific permission tables take precedence over conflicting generic permission descriptions; contract fixture work is part of Task 1, before route implementation.

After plan approval, execute Native. Commit the plan/status changes first; preserve tests and execution evidence as implementation progresses. Detailed B/C plans follow A rather than being inferred from this plan.
