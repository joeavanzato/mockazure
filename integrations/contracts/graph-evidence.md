# M3B evidence, licence and managed-identity contract

**Status:** Approved implementation contract, 2026-10-16; implementation in progress.
Part of the [M3B implementation plan](../../docs/superpowers/plans/2026-10-16-mockazure-evidence.md).
Microsoft endpoint facts and deliberate local modeling choices are distinguished below.
Existing M2/M3A contracts remain unchanged unless a specific extension is listed.

## 1. Authorization and licence matrix

Every route validates Graph audience, signature, expiry, current identity state and tenant
before operation checks. Invalid tokens are 401 InvalidAuthenticationToken. Denied scope,
role or licence is 403 Authorization_RequestDenied. Licence-denial messages identify the
required plan without revealing another tenant/object. Authorization precedes lookup.

Permission alternatives below are OR; delegated access additionally requires a listed
current role. Global Administrator is included in each privileged role set. The profile
does not implement custom roles or agent-identity permissions. Personal accounts remain
unsupported.

| Operation | Permission alternatives | Delegated roles | Licence |
| --- | --- | --- | --- |
| Tenant signIns list/get, v1.0/beta | AuditLog.Read.All | Global Reader, Reports Reader, Security Administrator, Security Operator, Security Reader | P1 or P2 |
| Own signIns list/get | Any nonempty delegated Graph scope; forced subject restriction | None | P1 or P2 |
| directoryAudits list/get, v1.0/beta | AuditLog.Read.All, Directory.Read.All | Reports Reader, Security Administrator, Security Reader | Free/P1/P2 |
| riskyUsers list/get, v1.0/beta | IdentityRiskyUser.Read.All | Global Reader, Security Operator, Security Reader, Security Administrator | P2 |
| riskDetections list/get, v1.0/beta | IdentityRiskEvent.Read.All | Global Reader, Security Operator, Security Reader, Security Administrator | P1 or P2; disclosure below |
| riskyUsers/confirmCompromised or dismiss, POST v1.0 | IdentityRiskyUser.ReadWrite.All | Security Administrator | P2 |
| subscribedSkus GET v1.0 | LicenseAssignment.Read.All, Directory.Read.All, Directory.ReadWrite.All, Organization.Read.All, Organization.ReadWrite.All | Global Reader, Directory Readers; Dynamics 365 Business Central Administrator for declared standard fields | No premium requirement |
| users/{id-or-UPN}/licenseDetails, me/licenseDetails GET v1.0 | LicenseAssignment.Read.All, Directory.Read.All, Directory.ReadWrite.All, User.Read.All, User.ReadWrite.All; User.Read only for self | Guest Inviter, Directory Readers, Directory Writers, License Administrator, User Administrator | No premium requirement; delegated only |
| users/{id-or-UPN}/assignLicense POST v1.0 | LicenseAssignment.ReadWrite.All, Directory.ReadWrite.All, User.ReadWrite.All | Directory Writers, License Administrator, User Administrator | Valid subscribed SKU |
| usageLocation create/PATCH | Existing ordinary-user write rules | Existing ordinary-user write roles | None |

Use tenant-wide access when its complete permission/role combination passes; otherwise
an eligible delegated sign-in request uses the self-only branch. Caller query is always
ANDed with that server-side userId predicate. Own-access GET of another record is 404.
Service-principal/managed-identity rows with no userId never enter the self branch.
No parameter chooses the authorization branch. Changing scopes, subject, roles or licence
invalidates or denies previously issued state as appropriate.

Endpoint documentation differs: directoryAudits explicitly lists Directory.Read.All,
whereas the current signIns permission table lists AuditLog.Read.All only; do not infer
one route from the other. licenseDetails currently declares app-only access unsupported:
return 403 even for an app with broad directory permissions. This is a documented profile
based on the cited endpoint tables, not a statement that all Microsoft deployments match.
[S1][S2][S3][S4][S5][S6][S7]

## 2. Licence state and field disclosure

Seed supported standalone products from Microsoft's identifiers:
- AAD_PREMIUM SKU 078d2b04-f1bd-4111-bbd4-b4b1b354cef4; P1 plan
  41781fb2-bc02-4b7c-bd55-b576c07bb09d.
- AAD_PREMIUM_P2 SKU 84a661c4-e949-4bd2-a560-ed7766fcaf2b; P2 plan
  eec0eb4f-6444-4f95-aba0-50c24d67f998.

Store full supplied SKU/service-plan inventory; only supported plan GUIDs grant these
entitlements. Do not infer entitlements from display names. SKU must be Enabled with
positive enabled seats and plan provisioningStatus Success. P2 satisfies tenant P1
availability. User effective plan set is the union of enabled directly assigned plans;
disabledPlans removes only that assignment's contribution. [S8]

Local operational policy: tenant inventory controls route availability; detailed user risk
requires both P2 inventory and the affected user's effective P2 assignment. For orphaned
historical users, use a validated import entitlement snapshot for that event, capped by
current tenant inventory. This deliberately explicit local enforcement is not a simulation
of Microsoft's billing/compliance systems. App-only subjects need no user licence.

P1 sign-ins expose riskDetail, riskLevelAggregated and riskLevelDuringSignIn as hidden.
For user records without effective P2, apply the same masking even in a P2 tenant.
Risk-event types for premium observations become generic and additionalInfo removes premium
details. Retain riskState where documented; never invent hidden as a riskState enum.
Provider predicates on masked fields evaluate the masked SQL expression, not stored
secrets, so eq/filter cannot be used to recover undisclosed details. [S9][S10]

Risk detections require P1 or P2; P2 gives supported detailed records. Initial controlled
risk types are generic, anonymizedIPAddress, unfamiliarFeatures and leakedCredentials.
The two premium types anonymizedIPAddress/unfamiliarFeatures become generic below
effective P2; suppress their additionalInfo. leakedCredentials retains its nonpremium
type. All types have fixed validated schemas; unknown controlled-injection types fail.
Historical import can use unknownFutureValue, without arbitrary privileged properties.
This bounded type catalog avoids claiming Microsoft's risk engine. [S4][S10]

No appliedConditionalAccessPolicies or invented device/geolocation claims are emitted
for generated events in B. conditionalAccessStatus=notApplied reflects the absence of
enforcement. Imported CA policy details are rejected until C provides the property
authorization contract.

## 3. Read schemas and query contracts

All resources include id and @odata.type. Explicit field allowlists:
- Sign-in common: createdDateTime, userId, userDisplayName, userPrincipalName, appId,
  appDisplayName, servicePrincipalId, servicePrincipalName, resourceId, resourceDisplayName, ipAddress, clientAppUsed, correlationId,
  isInteractive, status{errorCode,failureReason,additionalDetails}, conditionalAccessStatus,
  riskDetail, riskLevelAggregated, riskLevelDuringSignIn, riskState, riskEventTypes_v2,
  deviceDetail{deviceId,displayName,operatingSystem,browser,isCompliant,isManaged,trustType},
  location{city,state,countryOrRegion,geoCoordinates{latitude,longitude,altitude}}.
- Beta additionally: signInEventTypes,
  authenticationProtocol, authenticationRequirement, authenticationDetails. Details accept
  only authenticationStepDateTime, authenticationMethod, authenticationMethodDetail,
  succeeded, authenticationStepResultDetail, authenticationStepRequirement.
- Directory audit: activityDateTime, activityDisplayName, category, correlationId,
  loggedByService, result, resultReason, operationType, initiatedBy{user,app},
  targetResources, additionalDetails. User actor: id,displayName,userPrincipalName,ipAddress;
  app actor: appId,displayName,servicePrincipalId,servicePrincipalName. Target:
  id,displayName,type,userPrincipalName,groupType,modifiedProperties{displayName,oldValue,newValue}.
- Risky user: userDisplayName,userPrincipalName,isDeleted,isProcessing,riskLevel,riskState,
  riskDetail,riskLastUpdatedDateTime.
- Detection: userId,userDisplayName,userPrincipalName,riskEventType,riskLevel,riskState,
  riskDetail,activity,activityDateTime,detectedDateTime,lastUpdatedDateTime,detectionTimingType,
  source,ipAddress,location,requestId,correlationId,additionalInfo,tokenIssuerType.
- SKU: skuId,skuPartNumber,capabilityStatus,consumedUnits,prepaidUnits{enabled,suspended,warning},
  appliesTo,servicePlans{servicePlanId,servicePlanName,provisioningStatus,appliesTo}.
- Licence detail: skuId,skuPartNumber,servicePlans with the same declared service-plan fields.
- User selected extensions: usageLocation,assignedLicenses{skuId,disabledPlans},
  assignedPlans{assignedDateTime,capabilityStatus,service,servicePlanId}.

Generated unknown details are null/empty typed values, never guessed. Input schemas reject
unknown keys, NUL, invalid UTF-8, invalid enum/timestamps and fields outside the route version.
Enums accept documented values plus unknownFutureValue only where the type supports it.
Store raw public observations separately from versioned risk overlays. [S9][S11][S12][S13]

| Resource | Query options | Filter subset | Order/page |
| --- | --- | --- | --- |
| signIns | filter, top, skiptoken; createdDateTime orderby supported per resource doc | eq on scalar identifiers/names/IP/client/risk/state/correlation; time eq/ge/gt/le/lt; status/errorCode eq; startswith on UPN/display names/IP; beta event-type any with eq and OR | createdDateTime desc default, asc allowed, id tie-break; 1000 default/cap |
| directoryAudits | filter,top,orderby,skiptoken | activityDateTime ranges; eq category/result/name/correlation/id; startswith activityDisplayName and initiatedBy/user/userPrincipalName; actor id/appId eq; targetResources/any on id/type/UPN eq | activityDateTime desc default, asc allowed; 100 default/1000 cap |
| riskyUsers | filter,select,top,skiptoken | eq/ne id,riskLevel,riskState,riskDetail,isDeleted,isProcessing; riskLastUpdatedDateTime ranges | id asc; 100 default/500 cap |
| riskDetections | filter,select,top,skiptoken | eq/ne id,userId,riskEventType,riskLevel,riskState,riskDetail,activity; three time fields ranges | detectedDateTime desc then id; 20 default/500 cap |
| subscribedSkus,licenseDetails | select only | none | stable id order; inventory bounded to 100 SKUs |

Every collection admits and/or and parentheses over supported predicates. Unknown fields,
options or operators return 400 Request_UnsupportedQuery. No expand/count/delta/search,
offset skip or directory advanced-query header requirement for these evidence routes.
Structured any binds only the declared variable/child fields; no arbitrary JSON paths.
Existing limits remain: 16 KiB filter, depth16,256 nodes,100 in-values where in is supported.
A top beyond the route cap is capped; zero/negative/noninteger rejected. Item GET supports
no filter/paging; select only for risk routes. Requested projection includes id and type.

The sign-in list and resource docs differ on orderby; support only createdDateTime as
declared by the resource. v1.0 exposes interactive only; beta defaults to interactive
unless an explicit signInEventTypes predicate exists. The predicate still evaluates normally.
No service-principal or managedIdentity row is disguised as a v1.0 user sign-in. [S1][S2][S9]

## 4. Mutations and expected effects

### Direct licence assignment

POST /v1.0/users/{id-or-UPN}/assignLicense accepts addLicenses (default empty) and required
removeLicenses (may be empty), no other fields. Each add has skuId and disabledPlans
(default empty). Local bounds: at most20 distinct SKUs per request,100 disabled plans/SKU,
1 MiB JSON. Duplicate/overlapping add/remove SKU identifiers are 400 Request_BadRequest.
Return 200 with authorized current user including assignedLicenses/assignedPlans.

Only ordinary local Member targets, as in A. usageLocation must be a supported two-letter
ISO country code, stored through existing create/PATCH routes. Maintain an explicit
versioned country-code catalog; SKU allowed_usage_locations is an optional admin-configured
subset. Unsupported SKU plan dependencies fail validation. Built-in P1/P2 profile has no
simulated Exchange/other workload provisioning. Missing location, invalid plan/SKU,
unsupported target or insufficient seats returns 400 Request_BadRequest without partial
state. Assigning an already-held SKU updates disabled plans without consuming another seat;
removing an unassigned known SKU is idempotent. Repeating identical state emits no new
directory version/outbox. Enabled plans union across remaining SKUs determines access.
Deletion releases that user's seats atomically. Inventory cannot be shrunk below consumption.
[S7][S8]

### Risk actions

POST /v1.0/identityProtection/riskyUsers/{confirmCompromised|dismiss} accepts only userIds,
1..100 distinct UUIDs. Require all targets to be tenant-local existing risky users; reject
mixed invalid lists atomically with 404 Request_ResourceNotFound (local atomicity profile).
Return 204, no synthetic response body. No provider token gets administrative bypass.

confirmCompromised: user riskLevel=high,riskState=confirmedCompromised,
riskDetail=adminConfirmedUserCompromised. User action does not independently confirm every
historical sign-in/detection; only aggregate user state changes.
dismiss: user riskLevel=none,riskState=dismissed,riskDetail=adminDismissedAllRiskForUser;
close that user's unresolved atRisk/confirmedCompromised linked detections and sign-in
aggregate risk at the operation cutoff, preserving riskLevelDuringSignIn and original
observation facts. Later detections can raise risk again. Version all affected projections
with the same visibility revision; snapshots already issued remain unchanged.
Repeated no-op actions do not fabricate duplicate changes.

The exact linked-record closure cutoff and atomicity are declared local modeling choices;
the user transition/enum meaning follows the documented APIs. Keep an immutable action
record. Project a coarse UserManagement / Update user audit with those public risk
differences, loggedByService=Identity Protection. This is explicitly a MockAzure activity
mapping; no claim of byte-identical Microsoft risk-action audit labels. [S5][S14][S15]

## 5. Authentication and directory event mapping

Use stable source IDs, captured actor kind and nonsecret event-time labels. Directory
outbox v2 adds actor and target snapshots plus correlation_id. UUID fallback correlation
is UUIDv5(source_id,"correlation"). Sign-in, directory-audit and risk-detection record IDs
are UUIDv5(source_id,"mockazure:m3b:<family>:<ordinal>"); IDs remain stable across retries.
A riskyUser record instead uses the subject user ID, shared across its state versions.
Licence-detail IDs are stable UUIDv5(user_id,"license:<sku_id>"); skuId is the subscribed
product GUID, distinct from the subscribedSku resource ID. The local profile uses stable
UUID resource IDs and validates imported IDs accordingly.

| Committed action | Audit category/activity |
| --- | --- |
| user create/update/permanent delete | UserManagement: Add user / Update user / Hard Delete user |
| accountEnabled-only transition | UserManagement: Enable account / Disable account |
| group create/update/delete | GroupManagement: Add group / Update group / Delete group |
| explicit add/remove membership | GroupManagement: Add member to group / Remove member from group |
| licence change | UserManagement: Change user license |
| risk action | UserManagement: Update user (declared coarse mapping above) |

Combined profile/enable PATCH creates one Update user audit. Group creation embeds initial
members/owners in its Add group target differences, not fictitious separate API calls.
Deletion cascade edges stay facts on the delete audit. oldValue/newValue are JSON-encoded
strings; redact at source using declared public fields, not substring heuristics.
result=success; actor-less admin operations use initiatedBy={} and separate internal
origin metadata, never a fabricated Microsoft user. Directory dataset imports and empty
updates get receipts with zero provider projections. [S16]

| Terminal event | signInEventTypes | status.errorCode / capture |
| --- | --- | --- |
| authorization-code successful redemption | interactiveUser | 0 |
| device-code successful redemption | interactiveUser | 0; device approval itself emits no success |
| refresh successful issuance | nonInteractiveUser | 0 |
| client credentials successful issuance | servicePrincipal | 0 |
| local managed identity successful issuance | managedIdentity | 0 |
| known-tenant invalid username/password | interactiveUser | 50126, including unknown user with empty-string userId |
| disabled/deleted known user | flow's user type | 50057 / 50034 |
| explicit consent refusal | interactiveUser | 65004 |
| explicit simulated MFA refusal | interactiveUser | 50074 |
| invalid client secret on known registered client | servicePrincipal | 7000215 |
| disabled known app/SP | servicePrincipal | 7000112 |

Codes are evidence classifications, not a replacement of existing OAuth wire errors.
Invalid assertions map to 700027 only for signature/certificate validation failures;
malformed JWT syntax remains diagnostics. Revoked/expired registered credential maps to
7000222 when an expired secret is established, otherwise invalid-client diagnostics.
No guessing which credential matched. Unknown tenant/client, invalid CSRF/code/refresh
handles, polling, unsupported resource and signing/database faults produce no provider
sign-in. Existing refresh denial for a recognized disabled subject does produce 50057.
A bad managed-host header/unknown selector is a local host authorization failure, not an
Entra sign-in; disabled attached identity may yield managedIdentity failure7000112.
[S17]

Use request_id as a distinct attempt identity for failed password/refresh/token requests;
trusted browser transaction/device ID plus terminal step identifies one-time terminal
actions. The same consumed code/device grant cannot produce another successful event.
Success fact and token/session changes commit together. Persist known terminal failures
after rollback through a new transaction, then return the original sanitized failure.
If failure-fact persistence fails, return503. Never use unverified tid claims to place facts.

Backfill M2 outcomes only from stored facts: device_approval success is acknowledged with
no projection; device_code success projects once. No historical resource/UPN is inferred
from current mutable rows. Mark incomplete legacy provenance internally; nullable fields
remain null, and filters on unknown historical UPN cannot manufacture a match.

## 6. Managed-identity hosting protocol

Expose a separate host-only app: GET /MSI/token, configured host + port, separate from
public Graph/admin routing. Host credential hashes bind the request to one tenant/host.
Loopback default 127.0.0.1:49446; containers use isolated HTTP internal port8081.
HTTP here models the host-local protocol; provider/admin calls retain trusted local HTTPS.
No 169.254.169.254 binding, host networking changes or upstream calls. [S18]

Inputs: required api-version=2019-08-01 and resource; exactly zero or one of client_id,
principal_id,object_id,mi_res_id. principal_id/object_id are aliases, still mutually exclusive.
Reject duplicate keys and unknown parameters. Missing selector uses only the attached
system identity; an explicitly requested identity must be attached as user-assigned.
resource is an existing configured resource audience, not a URL to fetch.
Headers: exactly one nonempty X-IDENTITY-HEADER <=512 chars, compared via hashed credential.

Success: access_token,token_type=Bearer,client_id,resource,expires_on as decimal epoch
string. Reuse TOKEN_SECONDS=3600 and issuer/claims/signing machinery. Extra claims include
xms_mirid from stored identity resource ID. Do not accept caller-supplied roles/tenant.
Read only application grants, and identify managed identity by its persisted source,
not by the app-only token shape. No secret/certificate credentials for managed client_mode.

Errors use sanitized JSON {statusCode,message,error,error_description}, Cache-Control:no-store.
statusCode is the HTTP status integer and message is the sanitized description; these
host-protocol fields are consumed by the pinned MSAL App Service implementation.
error/error_description additionally expose the declared local diagnostic classification:
400 invalid_request for malformed version/selectors;400 invalid_resource for an unconfigured
audience;403 access_denied for absent/bad header;400 invalid_request for unknown/unattached
selector or missing system identity (same response across tenants);403 access_denied for
disabled binding/identity;503 temporarily_unavailable for storage/signing failure.
These error details are the explicit local profile where the public host protocol does
not specify exact messages.

Host-bound header issuance/rotation is admin-only, written once to caller-selected private
file, never stdout/public datasets/logs. Rotation blocks future acquisitions, not already
issued tokens. Detaching system identity disables it; detaching a user-assigned identity
only removes that host attachment. No disable on other hosts. Persisted app/SP disabled
state continues to deny resource calls under existing rules.

Preserve MSAL1.39.0 and Azure Identity1.26.0. The installed SDK maps resource_id to mi_res_id
and object_id to the host selector; validate all four HTTP selector forms plus real
ManagedIdentityCredential default/client_id/object_id/resource_id acquisition. Clear other
credential-chain environment selectors in isolated client processes. Do not use
DefaultAzureCredential or substitute a token-returning mock transport.

## 7. Administrative inputs and durability limits

All paths below prefix /_mock/tenants/{tenant_id}; existing management bearer required.
JSON rejects extras and validates UUIDs, aware UTC times and size before processing.

| Method/path | Input and result |
| --- | --- |
| GET/PATCH /evidence/settings | delay_seconds0..3600(default5),retention_days1..3650(default30),clock_mode wall/fixed,fixed_time when fixed; return settings |
| POST /evidence/clock/advance | seconds0..31536000; fixed clock only, reject rewind; return evidence time |
| POST /evidence/drain | max_jobs1..1000(default100); run existing fenced handler; return processed,pending,failed |
| POST /evidence/cleanup | max_rows1..1000(default500); return deleted/retained counts |
| POST /evidence/reset | expected_generation UUID; atomically invalidate only evidence/risk state and mark prior sources suppressed; keep directory,licences,auth; return new generation |
| GET /evidence/jobs | state optional,limit1..100(default50),opaque continuation; sanitized typed job views |
| POST /evidence/jobs/{id}/retry | failed evidence.project only; fence prior claim with monotonic epoch,reset attempts,retain source ID |
| POST /evidence/import | schema_version1,provenance{source,description,seed,generator},records[];10MiB/10000; atomic idempotent same-content,409 conflicting ID |
| GET /evidence/export | family optional,from/to optional UTC; fixed snapshot streamed JSON dataset; no bearer handles or secrets |
| POST /evidence/risk-observations | user_id,optional sign_in_id,event_type,level,occurred_at,request_id; atomic idempotency by request_id; return detection_id,user_id |
| PUT /licensing/skus/{sku_id} | sku_part_number,status,enabled/suspended/warning counts,service_plans[],allowed_usage_locations[]; no shrink below consumption |
| GET /licensing/skus | bounded stored inventory |
| POST /managed-identities | display_name,kind system/user,resource_id; create shared app/SP + typed MI row; return IDs |
| POST /managed-identity-hosts | name,optional system_identity_id,user_identity_ids[]<=100; return host_id + one-time header |
| PATCH /managed-identity-hosts/{id} | enabled,system_identity_id nullable,user_identity_ids[] replacement; same-tenant validation |
| POST /managed-identity-hosts/{id}/rotate-header | no body; return one-time new header |
| PATCH /managed-identities/{id} | enabled only; updates shared identity state |

Host creation/rotation responses are private administrative provisioning, never evidence
exports. Their CLI wrappers require --output and create private files; summarize IDs only.
Risk observation requires an active same-tenant user and, when specified, existing sign-in
for that user. Imported history can reference explicit absent/deleted subjects; if an ID
is known to belong to another tenant, reject. Absence is not permission to create identities.

Evidence import record: family signIn/directoryAudit/riskDetection/riskyUser; payload
matching section3; occurred_at,available_at; origin historical and optional entitlement
snapshot P1/P2. available_at cannot precede occurred_at. Cross-links use source event ID
and tenant; provenance fields have bounded strings and no nested arbitrary data. Never
accept passwords, tokens, keys, session data, headers or executable expressions.
Licence inventory/assignments are not silently inferred from imported evidence.

All new CLI groups use the API: evidence settings/clock/drain/jobs/retry/import/export/
cleanup/reset; licensing sku put/list; managed-identity create/enable;
managed-identity host create/update/rotate. Reset requires the generation argument.
Directory dataset v1 remains its existing public field schema: new licence/usage fields
are projected out of v1 export explicitly and documented, not accidentally serialized.
A full state backup remains the existing separate operational concern.

## 8. Publication, versioning and replay

EvidenceState contains generation, source_revision and publication_revision. Both counters
advance under a per-tenant row lock held through commit. Typed sources have unique
(tenant_id,source_kind,source_id), immutable facts and source_revision. Adapters copy
directory/auth facts in bounded transactions; no raw bearer values.

Use one immutable observation plus version rows keyed by family,record_id,valid_from with
valid_to, payload and indexed event_time/user/app/type. Projection inserts/overlays and
receipt/source acknowledgement commit with fenced job completion. Reserve publication
revision only in that transaction. available_at is captured from occurrence + delay;
visibility requires committed publication and available_at <= captured evidence time.

Risk actions update current state and version overlays atomically. A later arriving older
sign-in projection must consult the stored subject action cutoff so it cannot undo an
already-committed dismissal. Capture the action's evidence time and source revision.
Earlier-occurring observations remain closed even if imported/projected after dismissal;
new observations occurring after the cutoff can raise risk. Equal-time native observations
use their source revision to break ties; equal-time historical imports are treated as
pre-action observations. New native source revisions are captured at operation commit,
not assigned later by a scanner. This event-time policy is a declared local modeling choice. Multiple jobs may run across tenants; mutation/publication locks are short.

Lock order when combined: DirectoryState, SKU rows in UUID order, EvidenceState, job fence.
Never hold a claim transaction open while projecting. Administrative retry uses a monotonic
claim_epoch so resetting three-attempt budgets cannot make a stale lease valid again.
Leases30s, transient attempts3 with1s/2s retry delay, scanner batch100, projection batch1
source, export query chunks500. Poison schema errors are terminal without repeated retries.

Continuation expiry is one wall-clock hour, independent of fixed evidence time. Bind
generation/query/principal/permission hash and store snapshot revision/time/retention.
Use a separate evidence cursor table; reuse encoding/binding/hash helpers, not directory
generation or directory cleanup. Persist hashes only; deterministic replay next handles.
Current entitlement/role checks precede as-of reads. Record redaction entitlement fingerprint;
changed disclosure level rejects the old cursor with400 Request_UnsupportedQuery rather
than mixing masked/unmasked pages. Unavailable/malformed/expired cursors use that same
error family, not directory delta410.

Retention predicates apply to fresh reads; physical cleanup retains versions pinned by
active cursors. Receipts survive while sources remain replayable, preventing resurrection.
Reset locks EvidenceState, changes generation and suppresses all prior-source publication;
stale workers and cursors cannot restore old evidence. Future live operations still project.
No tenant-global lock around network I/O.

## Sources

Sources reviewed 2026-10-16; local modeling choices above are normative for MockAzure.
- [S1: signIns v1.0](https://learn.microsoft.com/en-us/graph/api/signin-list?view=graph-rest-1.0)
- [S2: signIns beta](https://learn.microsoft.com/en-us/graph/api/signin-list?view=graph-rest-beta)
- [S3: directoryAudits](https://learn.microsoft.com/en-us/graph/api/directoryaudit-list?view=graph-rest-1.0)
- [S4: riskDetections](https://learn.microsoft.com/en-us/graph/api/riskdetection-list?view=graph-rest-1.0)
- [S5: risky users and action licence requirement](https://learn.microsoft.com/en-us/graph/api/riskyuser-confirmcompromised?view=graph-rest-1.0)
- [S6: licence details](https://learn.microsoft.com/en-us/graph/api/user-list-licensedetails?view=graph-rest-1.0)
- [S7: assign licence](https://learn.microsoft.com/en-us/graph/api/user-assignlicense?view=graph-rest-1.0)
- [S8: product/plan identifiers](https://learn.microsoft.com/en-us/entra/identity/users/licensing-service-plan-reference)
- [S9: sign-in resource](https://learn.microsoft.com/en-us/graph/api/resources/signin?view=graph-rest-1.0)
- [S10: risk detections and premium disclosure](https://learn.microsoft.com/en-gb/entra/id-protection/concept-identity-protection-risks)
- [S11: detection schema](https://learn.microsoft.com/en-us/graph/api/resources/riskdetection?view=graph-rest-1.0)
- [S12: audit schema](https://learn.microsoft.com/en-us/graph/api/resources/directoryaudit?view=graph-rest-1.0)
- [S13: SKU operation](https://learn.microsoft.com/en-us/graph/api/subscribedsku-list?view=graph-rest-1.0)
- [S14: dismiss risk](https://learn.microsoft.com/en-us/graph/api/riskyuser-dismiss?view=graph-rest-1.0)
- [S15: risk detail values](https://learn.microsoft.com/en-us/graph/api/resources/riskdetail?view=graph-rest-1.0)
- [S16: audit activities](https://learn.microsoft.com/en-us/entra/identity/monitoring-health/reference-audit-activities)
- [S17: authentication errors](https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes)
- [S18: managed-identity hosting protocol](https://learn.microsoft.com/en-us/azure/app-service/overview-managed-identity)

## Pinned structural authority

Implementation uses the actual Microsoft Graph CSDL from
[microsoftgraph/msgraph-metadata](https://github.com/microsoftgraph/msgraph-metadata/tree/34b8e9c29032c82352a6fd7138be0a864a842bb2/schemas),
revision34b8e9c29032c82352a6fd7138be0a864a842bb2, schemas/v1.0-Prod.csdl.
The full source SHA-256 is286cdc5d812b88bba9870b452d63d37aed925e6180541f20e034c949cd1816f0.
The separately extracted beta schema has its own source path/hash in the bundled reference.
This structural authority supersedes earlier handwritten field assumptions; REST operation
documentation still governs authorization, available record types and query behavior.

Concrete corrections: modifiedProperty uses displayName; v1.0 signIn includes
servicePrincipalId/servicePrincipalName; signIn.userId is nonnullable in both metadata
versions, and beta servicePrincipalId is nonnullable too. Unknown/unapplicable required
string identifiers use an empty string, never a fabricated UUID or null. Empty is a declared
local representation of an unknown identity, not a claim that authentication resolved it.
Internal nullable subject_id remains distinct from the wire property.
