# MockAzure M3B: Evidence and collector output design

Status: Proposed for written review, 2026-10-16. No M3B product code has been written.
Baseline: main at 4a5fedb81ac200d22e4ec767bba1143926fe303c, containing merged M2 and M3A.
Parent: [Stateful Graph delivery map](2026-10-16-mockazure-stateful-graph.md).
Execution preference remains Native; implementation requires its detailed plan.

## Outcome and existing agreements

Make ordinary local authentication and supported directory mutations produce durable
Microsoft-shaped evidence that real collection tools can retrieve. PostgreSQL remains
the source of truth. Permissions, delegated roles, licence restrictions, ingestion delay,
retention, pagination and worker recovery are observable behavior.

The agreed increment includes sign-ins, directory audits, risk inspection, licence state
and original pinned Hawk/Microsoft Extractor Suite (MES) output validation. Normal use
requires no scenario. Historical imports are optional administrative inputs with provenance.
The user clarified that missing implementation endpoints must be built or simulated when
needed for realistic API interactions; their absence is not a reason to make a capability
import-only. Supported calls must produce meaningful state transitions and downstream effects.

All work stays under D:/projects/mockazure. Do not access VTW. Preserve M1, M2 and M3A
runtime files, databases, images and networks. No real Microsoft tenant traffic, upstream
fallback or changes to host trust, DNS, hosts, firewall or profiles. Public documentation
and dependency acquisition remain separate from isolated runtime qualification.

## Approach

Use persisted evidence projections driven by committed operation facts. This extends
M3A's transactional outbox and M2's authentication outcomes. It is recommended because
evidence survives object deletion, replay, worker interruption and retention of directory history.

Two alternatives were considered:
- Direct synchronous evidence writes simplify initial plumbing, but couple every provider
  mutation to every evidence schema and make configurable ingestion delay awkward.
- Synthesizing evidence during GET avoids workers, but cannot faithfully represent historical
  actor/target values or stable collection across changes. It fails this milestone's purpose.

Keep indexed identity/time/type columns and validated JSON payloads for provider detail.
Avoid a universal event-sourcing rewrite, a second queue service or a detection engine.

## Provider scope

Each version has an explicit contract. Shared implementation does not imply identical
supported fields or operations.

| Surface | M3B contract |
| --- | --- |
| v1.0 auditLogs/signIns and individual IDs | Interactive user evidence; documented default ordering and bounded time/user filters |
| beta auditLogs/signIns and individual IDs | Interactive, noninteractive, service-principal and managed-identity evidence; explicit event-type filters |
| v1.0 and beta auditLogs/directoryAudits and individual IDs | Persisted directory activity, actors, targets, outcomes and public modified properties |
| v1.0 and beta identityProtection/riskyUsers and riskDetections, including individual IDs | Stored inspection data; operation-specific read permissions and licensed disclosure |
| v1.0 subscribedSkus | Stored subscriptions/service plans and consumed units, with supported projection |
| v1.0 users/{id}/licenseDetails and me/licenseDetails | Stored direct user assignments; delegated access following the endpoint contract |
| Existing users reads | Explicit selected assignedLicenses/assignedPlans projections, versioned when assignment state changes |
| v1.0 users/{id}/assignLicense | Add/remove direct SKU assignments and change disabled plans; validate capacity and supported prerequisites |
| v1.0 identityProtection/riskyUsers/confirmCompromised and dismiss | Permission-checked state transitions and linked risk projections |
| Local App Service managed-identity token endpoint | Real SDK token acquisition for attached system/user-assigned identities, followed by normal provider API calls |

Provide working API transitions for the operations above. Administrative provisioning
supplies the simulated hosting environment and subscribed SKU inventory; it does not
replace provider assignment or risk actions. Group-based licensing and real subscription
purchase/provisioning are outside this increment. Additional endpoint dependencies needed
to complete these flows must be implemented or explicitly simulated, not silently dropped.

M3C retains grants, roles, application credentials, authentication methods and conditional
access enforcement. M4 retains Exchange rules and asynchronous UAL search. M5 retains ARM
Activity Logs. Do not manufacture conditional-access evaluation results before M3C.

## Evidence production

### Directory audits

Project successful provider user/group create, update, delete and explicit membership
add/remove operations. Each source operation has stable projection identity; records include
event-time actor, target, operation, correlation ID and public old/new property values.
Password material and credential fields are never serialized. Rolled-back operations create
no success evidence. No-op updates produce no fabricated property changes.

Administrative directory imports establish initial state and provenance; they do not
masquerade as provider operations. Preserve their outbox facts but classify them as
administrative import rather than silently emitting Microsoft audit activities. Administrative
create/enable controls use an explicitly documented origin mapping, without inventing a
Microsoft administrator account.

M3A facts lack a reliable actor-kind discriminator and complete event-time labels.
Extend new facts with versioned nonsecret actor/target snapshots and correlation IDs.
For old committed pending entries, derive only what their stored facts prove. Use null
for unknown labels, a deterministic correlation fallback, and a migration provenance
marker outside provider bodies. Do not reconstruct historical names from mutable live rows.

A deletion can affect several membership edges; use an explicit activity mapping instead
of assuming every removed edge implies an independently issued Remove member request.
Directory audit output is not copied into UAL or Activity Logs.

Failed provider mutations remain API diagnostics in B unless a specific failure activity
is added to the contract with documentation and tests. Do not claim every 4xx is an Entra audit.

### Sign-ins

- Completed authorization-code and device-code authentication generate interactive evidence.
- Refresh-token issuance generates noninteractive evidence.
- Client-credentials issuance generates service-principal evidence.
- Managed-identity token issuance through the new local endpoint generates managedIdentity
  evidence; ordinary client-credentials authentication remains servicePrincipal.
- Known-tenant authentication failures such as invalid credentials, disabled identities,
  or explicit consent/MFA refusal produce documented failure records. Invalid syntax,
  unknown tenants, device polling and internal signing failures are not successful sign-ins.

Record one outcome for each documented terminal authentication attempt. Device approval
plus token redemption must not double-count the same interactive success. Persist terminal
failure facts despite authentication transaction rollback, without committing partial grants,
sessions or tokens. Use a stable attempt ID and a separately committed bounded failure fact;
successful issuance and its fact commit together. Storage failure returns an explicit
temporary failure rather than silently losing promised evidence.

Captured facts include known subject/client/resource identities, event time, outcome,
correlation and the supported MFA result. Client IP comes only from the trusted request
transport configuration. Do not invent geolocation, device compliance or real MFA details.
A redacted, validated import may supply historical detail for testing.

### Managed identity

Implement the documented App Service managed-identity HTTP token protocol, chosen because
genuine Azure Identity clients can select it through IDENTITY_ENDPOINT and IDENTITY_HEADER.
The local endpoint uses api-version=2019-08-01, a validated resource audience and the
documented mutually exclusive identity selectors. No selector selects the hosting binding's
system-assigned identity; user-assigned selectors must resolve an identity attached to that
same binding. Unknown, conflicting, disabled or foreign identities fail explicitly.

Persist host bindings, attached identities and a hashed local endpoint-header credential.
Reuse the shared service-principal, grant, signing and provider authorization machinery
without exposing a client secret or creating a second directory. The header credential
authorizes only its hosting binding. It never grants arbitrary tenant identity impersonation.

Return real locally signed access tokens with supported audience, expiry and identity
claims. A genuine pinned Azure Identity ManagedIdentityCredential must acquire a token
and use it for allowed and denied Graph calls, with exact managedIdentity sign-in evidence.
Test header rotation, disabled identities, missing attachments and cross-tenant selectors.
Preserve existing token lifetime/grant-revocation semantics.

Serve this host-local protocol on a separately configured loopback/internal endpoint;
do not bind or route the host's real metadata address. Configure SDK processes explicitly,
with no credential-chain fallback. This establishes one complete managed-identity hosting
profile; VM IMDS and other hosting protocols remain additional profiles, not fake aliases.

### Risk

Historical/test evidence can establish initial detections and risk state. Also implement
Graph confirmCompromised and dismiss with IdentityRiskyUser.ReadWrite.All, documented
delegated role/licence checks, bounded userIds and tenant validation. Confirm compromise
raises the targeted risk state; dismissal lowers it and updates linked risk projections
according to the frozen endpoint contract. Requests must change observable state, not
return empty success. Record supported activity evidence and prevent partial changes on
validation failure under the documented local atomicity profile.

Keep original observations immutable while versioning provider-visible risk state and any
linked detection/sign-in risk fields that the supported actions change. Subsequent fresh
reads see the transition; existing continuations retain their captured version. Risk state
changes alone do not enforce conditional access before M3C.

The simulator does not claim Microsoft's proprietary detection engine. An authenticated
administrative risk-observation operation can supply a controlled detection and link it to
a real local sign-in through the same state service. Imports supplement this live path.
No heuristic automatically labels every failed login as a particular Microsoft detection.

## Persistence and worker design

Create typed evidence envelopes, projection receipts, evidence visibility state,
versioned risk state, managed-identity host bindings and licence tables. Tenant constraints and unique source/record keys
are enforced in PostgreSQL. Keep the existing directory outbox as the source adapter;
authentication outcomes receive their own adapter into the common projection pipeline.

Generalize the export-specific job claim interface into an explicit kind registry:
tenant.export and evidence.project. Retain existing export payloads, CLI behavior and
result schemas. Unknown kinds fail visibly with sanitized errors. Never parse all jobs
as ExportSnapshot.

Durable enqueue deduplicates by source kind and source ID. A bounded scanner repairs
missing enqueue work from committed source rows. Workers claim with leases and fencing,
retry transient failures, and expose exhausted jobs for authenticated inspection/retry.
Projection insertion, its receipt, source acknowledgement and fenced job completion
commit atomically. A stale worker cannot publish after its lease was superseded.
Exactly one visible record per projection key is enforced by uniqueness, despite
at-least-once delivery. Lease expiry, not process memory, controls recovery.

Do not prune source facts required by pending jobs or dataset provenance. Keep a projection
receipt/tombstone for as long as its source is replayable so retention cleanup cannot
resurrect expired evidence. Domain evidence is separate from API diagnostic logs.

## Time, snapshots and cleanup

Use wall-clock authentication. A separate tenant evidence clock defaults to UTC wall time
and can be fixed/advanced through authenticated administration for deterministic tests.
Never let evidence-clock controls change token validity.

Persist occurred_at and available_at separately. Proposed local defaults:
- Five-second projection availability delay, configurable from zero to one hour.
- Thirty-day sign-in/directory-audit/detection retention.
- Risky-user current state persists until explicit administrative change/removal.
- One-hour evidence continuation lifetime.

These are disclosed MockAzure defaults, not a universal Microsoft retention claim.
Historical imports require explicit UTC times and cannot change credentials.

A page captures an evidence visibility revision, evidence-time cutoff and retention cutoff.
Use tenant commit-ordered publication revisions, not a sequence allocated before commit.
Thus a delayed or concurrently committed record cannot appear halfway through enumeration.
Indexed SQL keyset paging applies filtering and sorting before bounded fetches.

Continuation state binds tenant, client, subject, route/version, query, ordering, revision
and expiry. Recheck current authorization and entitlements on every request. Deleting an
identity cannot erase historical evidence or remove tenant isolation.

Cleanup respects active continuations for their bounded lifetime. Fresh reads enforce
current retention immediately; physical deletion waits for snapshot pins. Explicit reset
invalidates the tenant generation first. Expired/invalidated evidence continuations return
an explicit Graph-shaped error; no evidence delta or fabricated delta-resync contract.

## Query and wire contracts

Extend the existing typed AST/compiler, not a parallel string parser. Add startswith only
for declared string fields and structured any predicates needed by pinned MES. Escape SQL
wildcards as data. Preserve parser size/depth/node bounds and parameter binding.

Support event-time comparisons, principal/user/app identifiers, status and declared risk
fields; directory audits also support declared actor/target paths. Sign-ins retain the
documented interactive-only default; beta callers explicitly select additional event types.
Use deterministic ID tie-breaking for equal timestamps.

Route-specific contracts decide top, select, orderby and skiptoken support. Do not inherit
directory advanced-query requirements or allow expand/count/delta everywhere. Sign-in
default and maximum pages are 1,000; risk detections default to 20 and cap at 500.
Directory audits default to 100 with a disclosed local cap of 1,000.
Risky users default to 100 and cap at 500.

Reuse current request IDs, Graph errors, batch authorization and PostgreSQL shared
throttling. Add evidence-specific resource budgets without changing directory budgets.
Unknown fields, methods and query options fail explicitly.

## Permissions and licensing

Authorize each operation separately. Tenant-wide sign-ins use AuditLog.Read.All;
directory audits allow the documented AuditLog.Read.All or Directory.Read.All alternative.
Risky users and detections require their distinct IdentityRiskyUser.Read.All and
IdentityRiskEvent.Read.All permissions. Delegated calls also require supported current
directory roles. Add only the roles needed by this increment, without weakening M3A checks.

Model self-sign-in access as a distinct delegated, own-user-only path where supported by
the referenced contract. Force the subject predicate server-side; OR expressions and
continuations cannot widen it. It does not authorize tenant-wide collection.

Store subscribed SKUs/service plans and direct user assignments with tenant-constrained
references. Entitlement evaluation uses enabled plans rather than display names or a
single is_premium flag. Assignment and entitlement updates take effect on the next authorization check, including
continuations; they do not rewrite issued token permission claims.

Implement user assignLicense as a shared domain operation with addLicenses/removeLicenses,
validated disabledPlans, usageLocation and supported SKU prerequisites, transactional
capacity accounting and public version/outbox updates. Add the needed usageLocation
read/write contract instead of accepting an inert property. Lock inventory and assignments
so concurrent calls cannot over-allocate the same seats. Read projections, consumedUnits,
user delta and entitlement checks must agree after add/remove/update. Clearly reject
unsupported plan dependencies rather than pretending provisioning succeeded.

Freeze an endpoint/field licence matrix in the implementation plan from the linked primary
sources. Test Free, P1 and P2 profiles independently. Do not blanket-deny every evidence
route without P2: riskDetections currently documents P1 or P2 access. Detailed risk
disclosure and risky-user access have their own requirements. Denied versus redacted
responses must be explicit in that matrix, with unknown enum fields preserved only when
the schema permits them.

subscribedSkus and licenseDetails have different permission contracts; the current
licenseDetails reference declares application access unsupported. B does not grant it
just to simplify an app-only collector. Group-based assignments and their aggregation
are rejected as unsupported administrative inputs.

## Administration and data exchange

Provide authenticated tenant-scoped evidence settings, import/export, job inspection/retry,
cleanup and projection-drain controls through API and CLI. Drain runs bounded work through
the same fenced handlers as background workers; it is not a second projection implementation.

Use a separate version-one evidence dataset rather than changing the M3A directory format.
Import is atomic per request, at most 10 MiB and 10,000 records, with strict schemas,
stable IDs, explicit provenance and validated references. Duplicate IDs with identical
content are idempotent; conflicting content fails atomically. Provider risk actions and administrative
risk observations use the same versioned state service and cannot erase original observations.

Exports stream a fixed snapshot in bounded batches and exclude secrets/session material.
Historical references to deleted or absent objects are explicit event snapshots; they
cannot create active identities or cross-link another tenant. Importing evidence never
grants permission or asserts that a live operation passed authorization.

## Collector qualification

Keep the pinned revisions and existing integrity verification:
- Hawk a44536f2b4225dd8ea2e463db88d5db40cfac8c7:
  Get-HawkUserEntraIDSignInLog, original CSV/JSON and risky-record exports.
- MES 47565f7842784cd3667ff630e3dccb49fa3408ed:
  Get-GraphEntraSignInLogs for all four types and Get-GraphEntraAuditLogs.

Use the original helpers, serializers, filtering, paging and retry logic. Extend the
preparation manifest for required pinned Graph modules. The only collection transport
adapter may rewrite the exact approved Graph origin to the configured local origin,
preserving method, path, query and body and invoking the genuine Graph SDK request.
Reject unknown destinations and external redirects. Keep collector files unmodified.
Disable update/telemetry calls only through the documented preparation/isolation boundary.

Use a fixed recent test window, known IDs and sentinel fields, multi-page populations,
empty results, equal timestamps and intentionally risky/non-risky records. Compare exact
ID multisets and relevant fields in original output files; detect duplicates, truncation,
invalid serialization and overwritten pages. A collector printing success is insufficient.
Do not adopt Hawk's 14-day client clamp as a universal provider retention rule.

Test actual 429 handling and denied/licence cases. If an original collector fails,
record the failure and its concrete cause; do not patch its algorithm, invent missing
output or quietly weaken the gate. Check MES's filter grouping exactly as sent, including
target-resource OR behavior; never alter query meaning to match its prose.

## Completion evidence

The implementation plan must cover:
1. Upgrade populated M3A databases and drain committed pending facts without duplicate
   audits; preserve all existing authentication, directory, delta and export behavior.
2. Concurrent projection, lease takeover, stale-worker rejection, poison-job visibility,
   worker termination before/after commit, two API replicas and PostgreSQL restart.
3. Runtime mutation and authentication evidence with exact actor/target/outcome checks;
   secret exclusion and historical correctness after rename/delete/history cleanup.
   Acquire and use real managed-identity tokens; assign/remove licences through Graph;
   confirm/dismiss risk through Graph and verify state, permissions, delta and evidence effects.
4. Free/P1/P2, roles/scopes, own-user versus tenant-wide reads, cross-tenant IDs,
   permission/licence changes during paging and malformed query coverage.
5. Availability, retention, equal-time ordering, concurrent ingestion, continuation replay
   and physical cleanup without missing or newly inserted records in old snapshots.
6. Genuine native Python and container SDK reads plus original Hawk/MES output checks.
   Clean wheel CLI operation and native/container parity remain required.
7. A 5,000-user fixture and at least 10,000 evidence records for bounded SQL/paging checks.
   The 100,000-user/million-record throughput profile remains M6.
8. Isolated M3B runtime/database/image with external DNS, IPv4 and IPv6 blocked for
   qualification; preserved M1/M2/M3A instances remain untouched.
9. Full appropriate regressions, one independent whole-change review under the existing
   Native preference, verified fixes, coherent commits, push and reviewable PR. No merge.

## Research and implementation-plan boundary

Source inspection found existing directory facts, authentication outcomes and an
export-only worker. Collector inspection confirmed the routes and filter families above.
Public endpoint documentation was checked on 2026-10-16. This is design evidence, not
proof of implemented compatibility.

Before product implementation, the written plan must freeze the exact property/error/
role/licence matrices, audit activity mappings, authentication terminal-outcome mapping,
managed-identity host protocol/selector errors, direct-licensing prerequisites and risk
action effects on linked records,
admin request schemas and collector dependency list. It must resolve source discrepancies
explicitly and define failing tests for those boundaries. Do not substitute blanket
permissions or invented field values where a contract is missing.

Primary references:
- [v1.0 sign-ins](https://learn.microsoft.com/en-us/graph/api/signin-list?view=graph-rest-1.0)
- [beta sign-ins](https://learn.microsoft.com/en-us/graph/api/signin-list?view=graph-rest-beta)
- [Directory audits](https://learn.microsoft.com/en-us/graph/api/directoryaudit-list?view=graph-rest-1.0)
- [Risky users](https://learn.microsoft.com/en-us/graph/api/riskyuser-list?view=graph-rest-1.0)
- [Risk detections](https://learn.microsoft.com/en-us/graph/api/riskdetection-list?view=graph-rest-1.0)
- [Activity-log access](https://learn.microsoft.com/en-us/entra/identity/monitoring-health/howto-access-activity-logs)
- [Identity Protection Graph access](https://learn.microsoft.com/en-us/entra/id-protection/howto-identity-protection-graph-api)
- [Subscribed SKUs](https://learn.microsoft.com/en-us/graph/api/subscribedsku-list?view=graph-rest-1.0)
- [User licence details](https://learn.microsoft.com/en-us/graph/api/user-list-licensedetails?view=graph-rest-1.0)
- [Assign user licences](https://learn.microsoft.com/en-us/graph/api/user-assignlicense?view=graph-rest-1.0)
- [Confirm compromised users](https://learn.microsoft.com/en-us/graph/api/riskyuser-confirmcompromised?view=graph-rest-1.0)
- [Dismiss user risk](https://learn.microsoft.com/en-us/graph/api/riskyuser-dismiss?view=graph-rest-1.0)
- [App Service managed-identity protocol](https://learn.microsoft.com/en-us/azure/app-service/overview-managed-identity)

Written design review is the next stage. Approval advances to the detailed M3B plan;
the prior Native execution preference is retained.
