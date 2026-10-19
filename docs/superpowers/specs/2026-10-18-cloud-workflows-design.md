# MockAzure cloud workflows and service breadth

Date: 2026-10-18

Status: specification approved by the user on 2026-10-18; implementation plans are being prepared for review. This document does not claim implementation or qualification of the proposed features.

Design baseline: qualified coverage expansion and coverage audit at commit `7660ca9`. This is a source baseline, not a claim that its branch is merged into main. Verify and reconcile main before implementation. Native execution remains the selected working style.

## 1. Intended outcome and agreed scope

Developers and security analysts must be able to exercise connected Microsoft cloud workflows locally with unchanged official clients: configure resources and access, perform operations, retrieve data, observe failures, and query resulting evidence. Breadth and complete workflows advance together across all families below. Scenario packs are optional fixtures; persistent operational state is the product model.

The user selected:

- Exchange audit search, configuration, recipient/delegation completeness, and security configuration.
- Azure provider discovery, resource paths, Storage accounts, Compute/network inventory, and diagnostic settings.
- Working Blob, Azure Files REST, Table, and Queue data planes.
- Key Vault secrets and their lifecycle.
- Graph mail, calendar, and change-notification workflows.
- Resource Graph and Log Analytics simulation with a broad, documented KQL subset.
- Required compatibility qualification in Python, PowerShell, .NET, and JavaScript/TypeScript.
- Microsoft Graph v1.0 schema and operation alignment for every Graph operation in this program.

SMB/NFS mounts are explicitly deferred. Compute and network support models resources, relationships and applicable lifecycle state; it does not execute guest operating systems or provide packet forwarding. No real Microsoft tenant is contacted. VTW is untouched. Preserve Apache licensing, Windows/Docker support, PostgreSQL, existing data, and previously accepted collector limitations.

## 2. Microsoft contracts are release requirements

### 2.1 Graph: structure and operation semantics

Use Microsoft's published v1.0 CSDL/type metadata and OpenAPI descriptions, plus the official v1.0 operation/resource/permissions documentation. Microsoft publishes metadata in [microsoftgraph/msgraph-metadata](https://github.com/microsoftgraph/msgraph-metadata), including the [v1.0 OpenAPI description](https://github.com/microsoftgraph/msgraph-metadata/blob/master/openapi/v1.0/openapi.yaml). The [v1.0 reference](https://learn.microsoft.com/en-us/graph/api/overview?view=graph-rest-1.0) supplies operation-level documentation.

For every new or changed public Graph operation, a checked-in contract record must identify:

1. HTTP method, canonical path templates, aliases, API version, and upstream operation identifier where available.
2. Request fields and action parameters: types, inheritance, nullability, enums, required fields, writable versus read-only fields, and documented extension/open-type behavior.
3. Response types, discriminators, annotations, collection envelopes, field omission rules, status codes, content types, and required headers.
4. Supported query options and operation-specific limits, paging/delta behavior where Microsoft defines them, and concurrency/precondition behavior.
5. Delegated/application permissions, applicable roles, resource access conditions, and documented licensing restrictions.
6. Official source URLs, immutable repository revisions where available, retrieval dates, file hashes, and the extracted schema dependency closure.
7. Supported behavior, explicit limitations, and test identifiers establishing conformance.

A CSDL type does not establish that an operation exists. A field appearing in a response model does not make it writable. A permissive JSON model does not authorize arbitrary properties. Follow the applicable operation contract rather than imposing one global unknown-property policy. Do not invent Graph fields, actions, relationships, delta routes, or beta-to-v1.0 equivalence. Keep emulator controls under the existing local administration interface.

Validation must cover request and response shapes as well as successful SDK deserialization. Include negative cases for invalid types, enum values, missing required parameters, unsupported options, insufficient access, and operation-specific errors. Preserve documented extensible enum/open-type behavior rather than tightening schemas arbitrarily.

Pin references before implementing each operation. Reuse the existing acquisition/validation mechanisms; reference updates are explicit reviewed changes. Runtime validation and CI must work from cached references without contacting Microsoft. Where official descriptions conflict or are incomplete, record the discrepancy and choose a documented bounded profile; do not present an inference as verified parity.

For example, [sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0) has action-specific JSON/MIME inputs and an empty `202 Accepted` response. Acceptance is distinct from delivery completion. Testing only the message resource schema would miss this behavior.

### 2.2 Other services retain their own contracts

Graph schemas apply to Graph, not to every Microsoft service:

| Family | Authoritative contract and versioning |
| --- | --- |
| ARM, provider resources, Resource Graph, Monitor | Versioned Microsoft Swagger/TypeSpec/OpenAPI sources in [Azure/azure-rest-api-specs](https://github.com/Azure/azure-rest-api-specs), with official operation documentation |
| Storage | Service-specific REST specifications, XML/JSON wire formats, request signing rules, service version headers, and official SDK behavior |
| Key Vault | Separate versioned management and secrets data-plane specifications |
| Exchange | Official Exchange Online command parameter sets, permissions and documented output behavior; pinned original module transport qualification |
| Log Analytics | Query API request/response contracts, official table references, and KQL language/operator documentation |

Version selection is per service and operation, pinned during the contract-catalog step. Do not invent one Azure-wide API version. Official SDKs are independent compatibility evidence, not the sole schema authority. Microsoft module behavior supplements incomplete Exchange output documentation without authorizing real-tenant probing.

## 3. Architecture and shared state

Keep the existing Python/FastAPI/SQLAlchemy/PostgreSQL architecture. Add focused domain modules rather than a universal JSON-resource endpoint. PostgreSQL remains authoritative for resources, authorization, content metadata, jobs, cursors, outbox records and queryable evidence. Reuse the existing durable binary content store behind service-specific adapters.

Each domain owns its invariants and lifecycle. Shared primitives cover tenant isolation, canonical ARM identity, transactions, content manifests, authorization evaluation, jobs, paging, fault injection and publication. External APIs retain their own serializers, error envelopes and protocol behavior.

A typed ARM provider registry binds namespace/type/version/operation to schema, handlers, RBAC actions, supported locations/SKUs, lifecycle rules, relationships and diagnostic categories. Unknown paths or unsupported versions must fail explicitly. Generic inventory and Resource Graph read the same canonical resources as provider-specific APIs.

Public changes commit state and native event facts atomically using the existing transactional publication pattern. Consumers project eligible facts into UAL, Activity Logs, service logs and change notifications. Preserve stable event identity and provenance; delivery retries must not fabricate new source actions. Not every operation belongs in every audit stream: event mappings require a service-specific contract.

Use durable jobs for applicable asynchronous operations, notification delivery and diagnostic publication. A worker restart resumes work safely. Reauthorize subsequent pages, job access and delivery eligibility as appropriate to the service contract. Never treat an imported descriptive permission object as an actual grant.

## 4. Functional scope by domain

### 4.1 Exchange audit, recipients and security

The initial command catalog includes:

- `Search-UnifiedAuditLog`, `Get-RemoteDomain`, and `Get-AdminAuditLogConfig`.
- `Get-Recipient`, `Get-EXORecipient`, `Get-EXOMailboxPermission`, and `Get-EXORecipientPermission`; consistent `GrantSendOnBehalfTo` recipient resolution.
- Remote-domain configuration changes and supported audit-configuration changes using documented cloud command parameter sets.
- Inbound/outbound connectors, authentication policies, organization relationships and sharing policies: reads and corresponding documented configuration mutations.
- Role-group membership and management-role assignment reads and changes needed to enforce the supported command set.
- Mailbox delegation changes needed to exercise SendAs, SendOnBehalf and applicable mailbox access workflows.

Keep the original ExchangeOnlineManagement module and existing approved bootstrap strategy. Do not replace compiled Microsoft commands with local client shims. Newly supported commands must work through the real module transport.

[Search-UnifiedAuditLog](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/search-unifiedauditlog?view=exchange-ps) searches canonical audit evidence with documented date/user/operation/object and other selected filters. Implement session identity, repeated-page advancement, result counts/indexes, limits and completion semantics independently from Graph's asynchronous search API. Preserve the expected AuditData serialization and record identity across interfaces. Apply current permissions and supported retention/availability rules.

[Get-RemoteDomain](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-remotedomain?view=exchange-ps) and [Get-AdminAuditLogConfig](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-adminauditlogconfig?view=exchange-ps) reflect persisted configuration. Cloud-only versus on-premises parameters must not be conflated.

Enforce configuration when the corresponding operation is simulated. Authentication policies do not implicitly control every modern OAuth flow. Connector/sharing settings that concern external transport or federation can be stored and queried, but their external execution is explicitly outside this local profile. Record those boundaries per field/operation. Full arbitrary custom Exchange RBAC and external mail routing are not implied by this command catalog.

### 4.2 ARM resources, Compute and Network

Support provider discovery/registration state, locations and published resource-type metadata for implemented providers. Extend existing subscription/resource-group/tag/RBAC behavior with typed resource create/read/update/delete operations and documented asynchronous polling for supported lifecycles.

Resource families include Storage accounts and service children; Key Vault vaults; Compute virtual machines, extensions and disks; Network virtual networks/subnets, network interfaces, network security groups/rules, and public IP addresses. Support inventory at appropriate scopes, relationships, VM instance-view and start/restart/power-off/deallocate simulation where contracted. Extension records model installation/provisioning state without executing extension payloads.

Validate parent existence, resource names, references, location compatibility, deletion constraints and authorization. Resource group deletion must account for newly owned data-plane resources and retained/recoverable data according to the supported service lifecycle. Provider registration, provisioning state and availability must remain coherent across reads and jobs.

Azure deployment-template execution, arbitrary provider pass-through, real VM execution and real network routing are not part of this program. The provider registry is extensible without returning synthetic success for unknown resource types.

### 4.3 Storage account management and four data planes

Account management includes resource CRUD/listing, properties, service endpoints, key listing/rotation, supported service/container/share configuration, and applicable access/network settings. Construct all endpoints from local configuration. Management access does not itself grant data access.

| Service | Required working profile |
| --- | --- |
| Blob | Containers; list/get/put/head/delete; metadata/properties; block staging/commit; ranged reads; conditional operations; leases; versions/snapshots and soft-delete behavior for the selected profile; prefix/delimiter/continuation listing |
| Files REST | Shares and directories; file create/delete/resize; range writes, clear ranges and reads; properties/metadata; list paging; share snapshots and applicable access conditions. No SMB/NFS mounts |
| Table | Table create/list/delete; typed entities keyed by PartitionKey/RowKey; insert/get/query/update/merge/upsert/delete; ETag conditions; OData filtering/projection; continuation tokens; supported same-partition transactional batches |
| Queue | Queue lifecycle and metadata; enqueue/peek/receive/update/delete/clear messages; visibility windows, expiration, dequeue counts and pop-receipt validation |

Service adapters must implement actual REST formats and version rules from the [Storage REST reference](https://learn.microsoft.com/en-us/rest/api/storageservices/). Internal chunk-upload protocols are not public Azure Blob or Files protocols. Use immutable content versions and transactional metadata to implement range writes and commits without corrupting concurrent readers.

Implement Shared Key, the applicable SAS forms, and Entra authorization only where supported by each selected service/version. Verify canonical signing, expiry/start times, permitted resources/actions and key rotation. User-delegation SAS is service-specific. Files OAuth support must follow the selected version's intent/permission requirements rather than inheriting Blob rules. Test raw signatures as well as official clients.

Separate network-rule configuration from enforceable local request context. Enforce rules only against trusted server-observed or explicitly provisioned local context; do not trust arbitrary client headers as proof of a virtual network/private endpoint. Clearly mark Azure fabric features that are represented but not executed.

### 4.4 Key Vault secrets

Support management-plane vault resources and data-plane secret set/get/list/update, version listing, delete/deleted-secret listing/get, recover and purge. Model secret attributes, enabled state, validity dates, content type, tags, recoverability and purge protection for the pinned API profile. Retrieval without a version resolves according to that contract; deletion/recovery preserves the correct version relationships.

Support the selected vault RBAC and access-policy modes with distinct management/data permissions. Changing permission mode must not leave stale grants effective. Secret values are confidential application data: exclude them from audit events, ordinary diagnostic output and default evidence exports. Explicit local backup/import mechanisms must distinguish secret-bearing data from ordinary evidence. This scope does not imply cryptographic keys, certificate issuance or HSM emulation.

### 4.5 Graph mail, calendar and notifications

All operations below require the v1.0 contract gates in section 2 before being advertised:

- Mail: draft creation/update, attachments including documented upload-session workflows, sendMail and draft send, reply/reply-all/forward and corresponding draft actions. Reuse existing folders, message content, moves, deletions and delta state.
- Calendar: calendars and calendar groups; event CRUD; calendarView and event instances; supported recurrence patterns, time zones and exceptions; accept/tentativelyAccept/decline/cancel; availability via getSchedule; calendarView delta where defined.
- Subscriptions: create/list/get/update/delete and applicable reauthorization; resource validation, expiry/renewal, validation handshake, clientState, local notification retries and lifecycle notifications for the supported resource profile.

Initial notification resources are supported mail, calendar and drive resources whose documented v1.0 subscription behavior can be implemented. Qualify each resource/changeType combination independently. Rich/encrypted resource-data notifications are explicitly deferred; reject unsupported requests for them. Do not invent subscription support for arbitrary routes.

Mail and meeting delivery operate between provisioned local recipients, using durable jobs. Sending to an address outside local provisioned delivery scope must follow a documented local simulation outcome without external delivery. Calendar invitations and responses update coherent organizer/attendee state. Distinguish accepted requests, delivery outcomes, notification delivery and audit publication.

Webhook destinations must be explicitly registered local test receivers, including named Docker services. Validate resolved destinations and redirects; do not allow arbitrary external delivery or credential leakage. The required protocol details come from the [subscription reference](https://learn.microsoft.com/en-us/graph/api/resources/subscription?view=graph-rest-1.0) and resource-specific notification documentation.

### 4.6 Resource Graph, Log Analytics and diagnostics

Resource Graph exposes the documented resources query endpoint over canonical ARM inventory with subscription scoping, output formats, pagination and explicit query limits. Log Analytics exposes its own workspace query/batch API and typed table response format. They share parser/evaluator components where semantics match; each service has its own allowed language profile, tables and authorization.

The broad KQL profile includes pipeline expressions; let bindings; where, project/project-away/project-rename, extend, distinct, sort/order, take/top, summarize, union, supported inner/leftouter/leftanti/innerunique joins, and mv-expand; common numeric/string/boolean/time expressions; dynamic JSON access, parse_json and casts; and count/countif/sum/avg/min/max/dcount/bin/arg_max aggregation patterns. Specify exact supported functions, join defaults, null/type/coercion behavior and approximation choices in the language catalog. Resource Graph must not accept operators solely because Log Analytics accepts them.

Compile a typed, allowlisted expression tree into parameterized PostgreSQL operations or a bounded evaluator. Do not interpolate raw query text into SQL. Enforce tenant/workspace/subscription filters before joins and aggregation. Define row, memory, time and expansion budgets. Unsupported syntax/functions fail with explicit errors; never silently drop clauses or return an empty success for a failed parse.

Workspace tables cover applicable native/imported Activity Logs, Storage service operations, Key Vault audit events and supported resource diagnostics. Pin table column/type mappings to Microsoft table references. Existing M365 audit facts remain in their appropriate stores unless an explicit ingestion mapping into a workspace is configured.

Implement diagnostic settings/category discovery and settings CRUD at supported resource/child-service scopes using the [Monitor contract](https://learn.microsoft.com/en-us/rest/api/monitor/diagnostic-settings/create-or-update?view=rest-monitor-2021-05-01-preview). Route eligible events into local Storage and Log Analytics destinations with category selection, destination authorization/validation, publication delay, retry and restart recovery. Resource-specific versus legacy table destinations must be labeled accurately. Event Hub delivery, arbitrary platform metrics and external destinations are not claimed in this profile.

## 5. Operational guarantees and local controls

- Persist state, jobs, cursors and publication progress across API/worker restart. Multiple API instances share one authority; use transactional concurrency controls and indexes by tenant/resource/time.
- Preserve service-specific ETags, conditions, error envelopes, retries and asynchronous polling. Return service-appropriate unsupported-operation errors rather than one universal response.
- Fault controls include deterministic throttling, transient failures, latency, asynchronous completion and publication delay. Defaults should permit predictable tests; timing controls remain local administration functions.
- Every new store participates in tenant reset, export/import, migration and reference-aware maintenance. Reset must cancel or invalidate old jobs/cursors/delivery work without leaking into a replacement tenant state.
- Never export credentials, account keys or secret values through ordinary evidence collection. Explicit local credential provisioning remains separate.
- Preserve old qualified behavior and label any changed contract or stricter validation. No silent data loss, hidden migration reset, or new real-service dependency.

## 6. Acceptance and coverage

An operation is supported only after its source contract, schema checks, semantic/permission tests and applicable wire/client checks pass. Each delivery includes positive and negative cases, cross-tenant isolation, restart behavior and data lifecycle checks. Verify unauthorized writes produce neither state changes nor false success audit records.

Python, PowerShell, .NET and JavaScript/TypeScript are all required client ecosystems. Use official clients where the service supplies them, with documented local endpoint/credential configuration. A missing SDK command is not replaced by a fake client: use that ecosystem's HTTP client for the operation and label it REST qualification. Exchange's original PowerShell module remains its required native client; there is no invented Exchange SDK requirement for every language.

Shared acceptance workflows include:

1. Create an account, grant data access, configure diagnostics, upload/download through Blob and Files, query emitted evidence, revoke access and verify denial.
2. Table entity conditional update plus same-partition batch; Queue receive/retry/visibility and deletion; restart between operations and verify persistence.
3. Create a vault/secret, read by permitted identity, create a new version, revoke access, delete/recover and enforce purge protection.
4. Create related Compute/Network records, change simulated lifecycle state, query relationships through Resource Graph, and collect the corresponding Activity Logs.
5. Change Exchange configuration/delegation, send local mail through Graph, retrieve matching audit evidence through supported UAL interfaces and test denied operations.
6. Create a recurring meeting, process a local attendee response, retrieve calendar changes, renew a subscription and observe webhook retry after receiver failure.
7. Query diagnostic evidence with joins, aggregations and dynamic fields; prove authorization scoping, unsupported-query failures and bounded resource consumption.

Client qualification is a matrix of ecosystem, service, workflow and pinned client version. Every ecosystem must pass representative Graph and Azure workflows; every service must have applicable official-client qualification. Do not describe a single-language pass as universal SDK support.

Scale qualification covers the existing 100,000-record and 256 MiB content baseline where applicable, multiple readers/writers, API/worker restart, paging completeness, stable byte hashes and isolation. Declare local resource/size limits separately from Microsoft service limits. Add workload-specific stress cases for joins, queue contention, file range writes and diagnostic fan-out.

Update COVERAGE.md in every implementing change with method/path or cmdlet, version, supported inputs/outputs, authorization, query behavior, persistence/mutations, evidence generation, client verification and limitations. Planned entries remain clearly planned. Generate/check an operation index from manifests to catch missing rows without equating router presence with functionality.

## 7. Delivery decomposition

This is a program specification. Detailed implementation plans are split by domain, with shared dependencies explicit. These are implementation phases within the current task, not requests to create separate chats.

| Phase | Deliverable | Dependency |
| --- | --- | --- |
| A | Contract catalogs and Exchange audit/configuration/recipient workflows | Current qualified baseline |
| B | Provider registry, Storage/Key Vault account management, Compute/Network resource inventory and lifecycle | Shared contract rules |
| C | Blob and Files REST working data planes with authorization/evidence | B and existing content store |
| D | Table, Queue and Key Vault secrets workflows | B; shared signing/data-authorization primitives |
| E | Graph mail/calendar workflows and local subscription delivery | Existing Graph/mail; A for Exchange effects |
| F | Broad query engine, Resource Graph, Log Analytics, diagnostic settings and local publication | B; service evidence from C/D; shared publication |
| G | Combined four-ecosystem qualification, scale/restart checks, package parity and coverage audit | A-F |

Contract inventory spans every agreed family from the outset. Each implementing phase lands usable breadth plus at least one complete workflow; qualification and documentation occur within phases, not solely in G. Diagnostic configuration and the query engine can be developed before all event producers, but delivery is only advertised once source-to-destination checks pass. No phase silently drops an agreed family.

Use cohesive commits and reviewable PRs targeting the current integration base. Verify actual ancestry and CI before calling work merged; avoid repeating the previous stacked-base ambiguity. Do not auto-merge. Preserve unrelated local changes. No product code is introduced by this specification.

## 8. Review outcome

Scope and architecture reflect the conversation, including REST-only Azure Files, broad KQL and four client ecosystems. The next artifact after approval is the implementation plan set with exact operation catalogs, pinned source acquisition steps, dependencies and qualification commands. Implementation starts only after that plan review.
