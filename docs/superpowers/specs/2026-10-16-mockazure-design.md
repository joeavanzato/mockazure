# MockAzure: Microsoft cloud API emulation and simulation

Date: 2026-10-16
Status: Design accepted for implementation planning. Implementation has not started.
Project: D:\projects\mockazure
Remote: https://github.com/joeavanzato/mockazure
Licence: Apache-2.0

## 1. Purpose and success

MockAzure is a general-purpose, stateful Microsoft cloud emulator. It prioritizes cloud evidence collection while supporting realistic API interaction, permission checks, policy enforcement and changes to simulated tenant state.

The primary experience is to start an instance, connect an ordinary client using local endpoints and credentials, and call supported Microsoft APIs. No incident scenario is required. A supported write changes the same state that subsequent reads observe and generates the relevant provider-shaped evidence.

The initial release covers Entra/Graph, Exchange/Microsoft 365 audit, and Azure Resource Manager/Activity Logs. It must run meaningful collection workflows from both Hawk and Microsoft Extractor Suite against the local emulator. It also supports direct SDK and REST clients. Compatibility is declared per operation and tested workflow, rather than as a claim to reproduce all Microsoft cloud behaviour.

VTW integration, migration, deployment recipes, consumer-specific regression suites and changes to VTW are outside this project design. The existing mock is an authorized source starting point, not an architectural constraint.

## 2. Boundaries and working rules

- Never edit, generate files, install dependencies, run tests or initialize tools inside E:\projects\vtw.
- All new project files, research downloads, generated credentials, data, exports and test output belong under D:\projects\mockazure. Container paths map to project-owned storage; an explicitly configured external PostgreSQL instance is also supported.
- The initial source import uses an explicit allowlist of tracked source files. Do not recursively copy the VTW directory, any untracked files, lab/.mockcloud/, deploy/secrets/, lab/*.env or other environment/secret files.
- The four original implementation files are the provenance baseline: lab/mockcloud/server.py, lab/mockcloud/certs.py, lab/mockcloud/__main__.py and tools/synth/cloud.py. Record their source revision and hashes during import. They may subsequently be reorganized or replaced.
- Do not preserve permissive authentication, provider inaccuracies or consumer-specific special cases solely for source compatibility.
- Runtime and integration tests never contact a real Microsoft tenant. There is no upstream fallback or transparent forwarding to Microsoft.
- Official documentation and public client source are acceptable research inputs. Dependency acquisition is a separate preparation step; compatibility runs operate with external service access blocked.
- Commit coherent changes frequently. Attach the supplied remote and push once access works, after checking remote history. Never force-push over unknown history.
- This document is a design, not authorization to skip the written-spec and implementation-plan review stages.

## 3. Product shape and packaging

Ship one Python implementation as a package, CLI and Docker image. Docker Compose is the primary quick start on Windows with Docker Desktop. The native Python/CLI installation has the same functionality and requires PostgreSQL; there is no SQLite alternative.

The initial deployment consists of an API process, a background worker and PostgreSQL. Processes use the same application package. API and worker replicas share durable state and can be increased independently. A development command may supervise the API and worker together without changing their responsibilities.

The CLI exposes lifecycle, tenant initialization, capabilities, endpoint/client configuration, certificate generation, import/export, reset, health and job inspection. A separate authenticated administrative API backs these operations. This control plane is independent of emulated Microsoft API permissions and must not be exposed as an anonymous reset endpoint.

Instances can initialize an empty tenant or a seeded starter tenant. Durable state is the default. Ephemeral test instances use disposable databases or isolated instance namespaces with an explicit cleanup lifecycle. A database migration command manages schema changes.

The repository will use src/mockazure for the application, tests for automated checks, integrations for client launchers and manifests, fixtures for optional datasets, and docs for compatibility and design material. Runtime files and acquired third-party tools stay in ignored project directories.

## 4. Architectural choice

Use a modular application with shared PostgreSQL persistence.

Extending the original four-file layout would entangle protocol handling, authorization, synthetic data and state. Splitting each Microsoft surface into a separately deployed service would add distributed coordination before it provides useful isolation. Module boundaries within one application provide clear ownership and transactional consistency while retaining simple installation.

| Component | Responsibility | Interface and dependencies |
| --- | --- | --- |
| HTTP and service routing | TLS-facing requests, host/service mapping, request context and version dispatch | Calls provider modules with validated request context |
| Identity and sessions | Applications, credentials, local users, token issuance, discovery and delegated sessions | Uses persistent identity/session state and signing material |
| Authorization and policies | Evaluate operation permissions, roles, scope, licences and supported policy conditions | Consumes identity context, operation requirements and tenant state |
| Provider modules | Entra, Graph, Exchange and ARM protocol semantics and representations | Call domain operations and repositories; do not implement isolated copies of tenant state |
| Domain operations | Apply validated mutations and enforce cross-object invariants | Commit through PostgreSQL transactions |
| Evidence and jobs | Provider-shaped audit records, delayed visibility and asynchronous collection jobs | Consume durable work; publish idempotently |
| Query and continuation services | Parse supported queries, compile bounded database queries and manage paging/delta state | Use provider capability definitions and repositories |
| Administration and fixtures | Tenant provisioning, inspection, reset, optional synthetic records and test controls | Authenticated management interface; separate from public provider routes |

Each module has an explicit contract and can be tested independently. Runtime behaviour depends on data and operation definitions rather than consumer names such as VTW blade identifiers.

## 5. Persistent state and scaling

PostgreSQL is the system of record for tenant resources, relationships, grants, role assignments, policy configuration, sessions, asynchronous searches, evidence, durable jobs, continuation state and change tracking.

Use typed columns for identifiers and frequently queried dimensions, with JSONB for provider fields that need flexible representation. Every tenant-scoped record and relationship carries its tenant identity. Relational constraints enforce ownership and referential integrity. Subscription/resource scope is represented explicitly for ARM authorization.

Index collection access by tenant and resource type, and evidence access by tenant, record type, event time and stable identifier. Add indexes for the filter combinations in supported workflows. Use database-side filtering, projection and bounded pagination. Do not materialize a tenant's complete evidence history in an API worker.

A resource mutation, its domain change record and its required evidence job are committed together. Workers claim durable jobs with leases, retry failed work and expose permanent failures. Processing is at least once; uniqueness and idempotency constraints prevent duplicate logical evidence. Workers recover abandoned jobs after a crash.

State, jobs and credentials survive restarts. Signing keys are instance-owned persistent material accessible to the relevant replicas; they are not regenerated at every process start. Replicas do not require sticky sessions or process-local paging/query state.

Continuation tokens bind to tenant, resource, API version and query context. They are opaque to clients, integrity-protected, bounded in lifetime and reusable across API replicas. Their snapshot and expiration behaviour follows the operation's declared contract. Mutable collections need an explicit consistency strategy; a simple offset over a changing list is insufficient.

Record changes in a durable journal so resource-specific delta support can represent additions, modifications and deletions. This is a change-tracking mechanism, not a requirement to rebuild all tenant state by replaying events.

High throughput is a validation goal, not a consequence assumed from using PostgreSQL. Benchmarks publish dataset size, hardware, worker counts, concurrency, throughput, latency and memory use. Query plans and database connection limits are part of scale qualification.

## 6. Authentication

Support app-only client credentials and delegated authentication in the first release.

App-only authentication validates registered client secrets and certificate assertions. Assertion checks include signature, registered key, issuer/subject, audience, validity period and replay protection. Secret storage uses appropriate one-way verification. Lab credential issuance is explicit and independent of real credentials.

Delegated authentication supports authorization code with PKCE and device authorization. Provide a minimal local sign-in/consent experience, scriptable test users and simulated MFA outcomes. Refresh-token handling, expiry, consent and session state are persistent. App-only clients do not receive delegated refresh tokens.

Publish internally consistent discovery metadata, issuer identifiers, signing algorithms and reachable JWKS. Use asymmetric signing with persistent keys and defined rotation behaviour. Validate issuer, audience, tenant, signature, token type, expiry and not-before conditions at resource endpoints.

Represent application permissions and delegated scopes separately. Delegated access also considers the user's applicable roles and access to the target resource. Do not treat a scope claim as universal permission.

Resource audiences and network destinations are separate concepts. A client may request a familiar Microsoft resource audience while all traffic goes to local configured endpoints. Allow only configured audience aliases; accepting an audience never authorizes an upstream request.

Grant changes, credential changes and token revocation obey explicitly documented semantics. Existing access tokens are not automatically rewritten when a grant changes. Tests distinguish token issuance checks, resource authorization and supported session/revocation checks.

Normal authentication uses wall-clock time to interoperate with ordinary SDKs. Time manipulation is restricted to controlled test modes, with the client and server clocks coordinated. Evidence time can be controlled independently.

## 7. Authorization, policy and licence enforcement

Each operation defines application permissions, delegated scopes, user-role requirements, resource scope and relevant licence restrictions. Apply these requirements to actual requests.

Graph, Exchange RBAC and ARM RBAC use service-appropriate rules. A Graph application grant does not imply Exchange management privileges or subscription access. ARM role assignments respect scope inheritance for the supported subscription, resource-group and resource hierarchy.

The first enforcement increment includes:

- Application permission and delegated-scope grants, consent and tenant boundaries.
- Relevant directory roles and Exchange roles for implemented operations.
- ARM role definitions and assignments for supported actions/scopes.
- Licence-dependent access for implemented evidence sources.
- A documented conditional-access subset for delegated sign-in: user/group and target-resource inclusion/exclusion, supported IP-location conditions, block access and require simulated MFA.

Policy coverage is explicit. The administration interface distinguishes enforced policies from inspectable imported policy data. It rejects enabling unsupported conditions in enforced mode. Inspect-only import is explicit and does not silently create a claim of enforcement. Report-only behaviour, where implemented, records the evaluated result without enforcing a block.

Do not apply delegated-user policy rules indiscriminately to workload identities. Additional policy families, device compliance, workload identity policy and complex grant combinations expand through separately documented increments.

## 8. Initial service coverage

This table defines the first-release families and minimum useful behaviour. Each delivery milestone must enumerate concrete routes, versions, fields, methods, permissions, query features and contract tests before those operations are implemented.

| Surface | First-release coverage |
| --- | --- |
| Entra | Instance/OIDC discovery, keys, client credentials, authorization code, device authorization, consent, sessions and relevant sign-in outcomes |
| Graph directory | Users, groups and membership, devices, applications, service principals, owners, application/delegated grants, directory roles/assignments, organization and subscribed licences |
| Graph security/configuration | Conditional-access policy inspection and the enforced subset; authentication-method inspection; risky users and risk detections |
| Graph evidence | Interactive/noninteractive user, service-principal and managed-identity sign-in records; directory audits; asynchronous security/auditLog/queries and records |
| Exchange/M365 | Mailbox configuration and forwarding, inbox rules including service-specific hidden-rule visibility, sweep rules, message-trace records and required Exchange administration requests |
| ARM | Tenants/subscriptions needed for client discovery, resource groups, generic resource inventory, role definitions/assignments and Activity Log queries |

Stateful operations initially exercise user/group lifecycle and membership, application/service-principal credentials and grants, directory-role assignments, the policy subset, mailbox forwarding/inbox rules, resource-group lifecycle/resource tags and ARM role assignments. Method-level scope is documented; listing a resource family does not imply all Microsoft operations on that resource.

API-backed credential and permission changes must affect real subsequent behaviour in the emulator. Read-oriented evidence families need not invent provider write endpoints: test seeding uses the administrative API and supported actions generate evidence naturally.

Device management begins with directory and managed-device inspection needed by supported clients. Full Intune management, Defender, broader Purview/eDiscovery, SharePoint/Teams workloads and actual Azure resource provisioning are later scope. Inventory objects do not imply execution of their underlying cloud services.

Implement Graph v1.0 and beta as explicit contracts. Shared implementations are appropriate only where the supported semantics match. ARM api-version values likewise have an explicit allowlist and capability matrix.

## 9. Query and protocol fidelity

Microsoft documents query support as operation- and version-specific. MockAzure follows that structure rather than enabling all OData features globally.

Build a real parser and typed expression representation for the supported filter grammar. Initial workflow needs include equality and time comparisons, booleans/null, conjunction/disjunction and grouping, in expressions, nested properties and collection any expressions. Validate fields, operators and complexity before compiling parameterized database queries.

Implement real select projection, ordering, top/page limits, count with applicable advanced-query headers, bounded expansion on declared relationships, and supported batch requests. Preserve service-specific envelopes, metadata, timestamps, null handling, request identifiers, headers and error shapes.

Initial expansion targets are supported directory relationships such as group members and application/service-principal owners. Unsupported expansion paths and unsupported nested query combinations fail explicitly.

User and group delta queries are the first change-tracking targets. Their exact selected fields, relationship support, tombstones, pagination, token expiration and resynchronization contract are defined in the Graph milestone. Other resources must not advertise delta merely because the underlying journal could support it.

Unsupported methods, resource paths, api-versions, query options or policy enforcement requests produce explicit errors. Where Microsoft specifies an error, reproduce its documented status/shape. Where the gap is emulator-specific, report it clearly and record the capability limitation. Do not return fabricated empty success.

Unknown response fields from fixture data cannot bypass schema or authorization rules. Requests cannot introduce arbitrary executable expressions or raw SQL through query syntax.

Throttle and transient-failure behaviour is configurable, with valid Retry-After semantics. Normal mode has documented service limits; deterministic fault injection is opt-in. The original hard-coded first-page/third-page fault pattern is not a universal runtime rule.

## 10. Evidence and asynchronous behaviour

Maintain a consistent identity and resource graph across service representations. Provider evidence references the objects, actors, correlation identifiers and outcomes of the relevant action.

Use a single committed domain operation to derive the appropriate directory audit, UAL or Activity Log projections. Do not emit every action into every log. Failed-operation and sign-in evidence follows the supported provider's documented behaviour. API diagnostics are separate from simulated cloud evidence.

Evidence has occurrence and availability times. Configurable delays model propagation and audit ingestion. Default delays are bounded and documented; tests can advance evidence time or run pending jobs deterministically.

UAL search is a durable job with creation, polling, completion/failure and record pagination. Searches remain accessible after an API or worker restart. Define how query results relate to their time window and availability watermark, and keep result paging stable for the declared job lifetime.

Persisted evidence has stable identifiers. Explicit retention settings and controlled cleanup apply to large histories and expired jobs/tokens. Reset affects the selected test instance or tenant according to its documented scope, without touching other tenants.

## 11. Administration, TLS and client integration

Use configurable local service hostnames and HTTPS. Generate a project-owned lab CA and certificates for configured names, with a documented renewal process. Never read or reuse the source project's generated certificates.

Expose separate advertised endpoints for container clients, host clients and browser-based sign-in where needed. Discovery, redirects, nextLink, deltaLink and asynchronous Location URLs use the configured service bases. Validate Host/forwarding information instead of reflecting arbitrary request hosts into trusted URLs.

Default published ports bind locally. Client configuration output includes endpoints, tenant/client identifiers, requested audience aliases and CA paths. It contains no real-tenant defaults. Trust-store installation is explicit; per-client CA configuration is preferred when supported.

Configuration-only integration is the target. A thin, versioned adapter is acceptable when a client hard-codes URLs or cannot configure local authentication. It may redirect endpoint addresses and establish a local authenticated context. It must not synthesize collection results, replace provider cmdlets with canned responses, skip server permissions or change collection algorithms.

Adapter acquisition/manifests pin tool and dependency versions or immutable revisions. Compatibility with the pinned SDK/module connection flows, especially Exchange connection bootstrap, is a milestone-2 feasibility requirement. If a chosen transport cannot be redirected faithfully, report the concrete limitation and revise the design before substituting another approach.

Client runs deny external service access, including automatic authentication fallback and telemetry. Dependency installation happens before isolation. No production-host DNS override is the default installation mechanism.

## 12. Optional synthetic data and test fixtures

Provide versioned YAML tenant definitions and optional incident/test fixtures backed by deterministic Python generators. Configuration can describe initial identities, grants, licences, subscriptions and resources. A starter dataset contains plausible normal activity as well as optional incident evidence.

Fixtures are utilities, not the product's central abstraction. Normal API use does not require a scenario or a fixed historical date window.

Distinguish:

1. Initial state and historical evidence loaded through an explicit administrative import.
2. Actions executed through the same enforcement and mutation paths used by API clients.

Only the second represents a live operation having passed current permissions and policy checks. Imports validate references and schemas and identify their provenance.

Optional ground-truth exports link fixture steps, objects and evidence IDs using tool-independent labels. They are separate from Microsoft response bodies. Preserve deterministic seeds and reproducible time windows for tests.

Export/import has a versioned format. Ordinary shareable dataset exports omit credentials, private keys and session secrets. A separately documented operational backup/restore procedure covers the database and required instance key/certificate material together, so restoring an instance preserves its authentication identity and full durable state.

## 13. Verification and release gates

Verification layers:

- Unit and domain checks for query interpretation, relationship invariants, authorization decisions and mutation/evidence transactions.
- Provider contract tests against documented API behaviour and explicitly maintained fixtures.
- PostgreSQL integration checks for migrations, isolation, persistence, job retries, concurrent writes and paging.
- Real Python and PowerShell SDK authentication/request/response tests.
- Pinned external-collector workflows with real network requests to the emulator and validation of output contents.
- Larger-dataset benchmarks and repeated multi-worker collection runs.

The first release must complete:

- Hawk: per-user Entra sign-in collection and Exchange inbox/sweep-rule collection, producing its normal output files.
- Microsoft Extractor Suite: Entra sign-in and directory-audit collection, Graph UAL query creation/polling/download, and Azure Activity Log export.

These are selected workflows, not a claim that the tools' collect-everything entry points are fully supported. Their transitive dependencies, connection probes and SDK bootstrap requests are included in the compatibility manifest.

Test authorized and denied access, wrong-tenant/resource tokens, revoked/expired credentials where applicable, licence restrictions, persisted state, pagination under concurrent changes, 429 recovery, failed asynchronous jobs and restart recovery.

Compare expected record IDs and relevant fields, not merely process exit codes or nonempty files. Verify that runtime mutations are observable through both resource reads and expected evidence.

Qualification datasets include a small deterministic case, a 5,000-user tenant, and a scale case targeting 100,000 directory users and one million evidence records. The scale sizes are validation targets, not throughput promises. Publish the actual tested profile and results; any inability to meet the agreed profile is reported before release.

Run with multiple API and background workers against the same database to validate shared state. Confirm paging continuity across API restarts and idempotent evidence production after worker interruption.

## 14. Delivery sequence

This is a multi-milestone project. The design is the overall contract; implementation is planned in bounded milestone-sized increments. Do not write one enormous implementation plan or treat source extraction as completion of the emulator.

| Milestone | Deliverable and exit condition |
| --- | --- |
| 1. Independent foundation | Source provenance import, Apache-2.0, package/CLI and Docker setup, PostgreSQL migrations, persistent tenant administration and TLS; local initialization/restart smoke checks |
| 2. Identity and connectivity | App-only and delegated authentication, discovery/keys/sessions, core permission checks, actual Python/PowerShell SDK connections and proven local connection paths for both collector families |
| 3. Stateful Graph | Directory/relationship reads and selected mutations, operation-specific query features, initial delta support, sign-ins/audits, enforced policy subset and Graph collector output checks |
| 4. Exchange/M365 audit | Required Exchange connection/command contracts, mailbox and rule state, message tracing, durable UAL searches and Hawk/MES workflow checks |
| 5. Azure management | Discovery, inventory, scoped RBAC, selected mutations, Activity Logs and MES Activity Log export |
| 6. Release qualification | All agreed collector gates, package parity, multi-worker/restart/load results, capabilities documentation and reproducible release artifacts |

Milestone 2 resolves connection feasibility before large provider expansion. Contract and integration tests are added with the relevant module, not deferred to milestone 6.

The detailed plan for each milestone enumerates routes, schemas, supported operations, dependency versions, fixtures, tests and completion commands. Later implementation choices may change internal code structure while preserving this design's external guarantees. Scope changes are made explicit rather than silently weakening release gates.

## 15. Research findings and compatibility implications

The reviewed source is useful for synthetic identities, evidence shapes and collection edge cases, but its current authentication, query parsing and in-memory state are inadequate for the agreed product. Its live control switches errors rather than producing new events. The new runtime owns its own implementation.

Hawk's sign-in function uses the Graph PowerShell SDK and its connection probe reads organization information before attempting a default connection. The local harness must provide that authenticated context and prevent real-service fallback. The inbox-rule function also requests sweep rules and consumes rule descriptions.

- [Hawk sign-in collector](https://github.com/T0pCyber/hawk/blob/master/Hawk/functions/User/Get-HawkUserEntraIDSignInLog.ps1)
- [Hawk Graph connection check](https://github.com/T0pCyber/hawk/blob/master/Hawk/internal/functions/Test-GraphConnection.ps1)
- [Hawk inbox-rule collector](https://github.com/T0pCyber/hawk/blob/master/Hawk/functions/User/Get-HawkUserInboxRule.ps1)

Microsoft Extractor Suite contains hard-coded ARM and Graph request bases; configuring an SDK environment alone may not redirect all paths. Its sign-in collection includes combined event-type expressions, and its UAL collector performs asynchronous search and paged result retrieval. Those behaviours motivate the adapter, parser and job contracts. This is source inspection, not demonstrated runtime compatibility.

- [MES connection wrappers](https://github.com/invictus-ir/Microsoft-Extractor-Suite/blob/main/Scripts/Connect.ps1)
- [MES Activity Log collection](https://github.com/invictus-ir/Microsoft-Extractor-Suite/blob/main/Scripts/Get-AzureActivityLogs.ps1)
- [MES Entra Graph collection](https://github.com/invictus-ir/Microsoft-Extractor-Suite/blob/main/Scripts/Get-AzureEntraGraphLogs.ps1)
- [MES Graph UAL collection](https://github.com/invictus-ir/Microsoft-Extractor-Suite/blob/main/Scripts/Get-UALGraph.ps1)

Provider documentation governs fidelity; a client's assumptions or comments are not themselves authoritative Microsoft API behaviour. Tests may expose a client defect without justifying an inaccurate emulator response.

Additional primary references:

- [Microsoft Graph query parameters](https://learn.microsoft.com/en-us/graph/query-parameters)
- [Microsoft Graph delta query](https://learn.microsoft.com/en-us/graph/delta-query-overview)
- [Graph PowerShell custom environments](https://learn.microsoft.com/en-us/powershell/module/microsoft.graph.authentication/add-mgenvironment?view=graph-powershell-1.0)
- [PostgreSQL SELECT and locking](https://www.postgresql.org/docs/current/sql-select.html)
- [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0)

## 16. Review and next step

The agreed product decisions are broad Microsoft API emulation, evidence-collection priority, stateful operational behaviour, both authentication modes, PostgreSQL, durable state, Docker-first/native parity, optional fixtures, explicit fidelity limits, both collector families as release gates, and Apache-2.0.

This written specification makes those decisions concrete, including proposed first-release operation families, enforcement boundaries and scale qualification targets. Review it before detailed implementation planning. Written approval leads to a milestone-1 implementation plan with its own review and execution-method choice. No application code has been written as part of this specification.
