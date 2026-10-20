# Evidence from living tenant operations

Business actions use the same native source and worker pipelines as ordinary API
requests. Importing fixture history does not impersonate a new API operation.
The versioned catalog in `src/mockazure/evidence/data/operation_mappings.json`
declares an evidence destination or an explicit limitation for every operation in
the living tenant API inventory. Planned APIs are not claimed as working.

| Native action | Evidence | Important limits |
|---|---|---|
| Authentication | Graph sign-ins | Existing known-identity and flow eligibility rules apply. |
| Directory, grants, roles and policies | Graph directory audits | No-op updates are suppressed; credentials are excluded. |
| Mailbox and calendar item changes | Purview audit records | Existing Exchange audit settings apply; sends emit when the worker commits. |
| File upload, replacement, rename, move, copy, delete | `FileUploaded`, `FileModified`, `FileRenamed`, `FileMoved`, `FileCopied`, `FileDeleted` | Files only; one event per committed action, without Microsoft's five-minute modification aggregation. Object IDs use the canonical item ID when no web URL exists. |
| Teams chat creation | `ChatCreated` | Local chat lifecycle only. |
| Teams team/channel/chat member addition and removal | `MemberAdded`, `MemberRemoved` | Local authoritative membership, not imported roster observations. |
| Teams team role change | `MemberRoleChanged` | Independent channel role changes retain a native fact without claiming this event. |
| Teams message edit and undelete | `GraphMessageUpdated`, `GraphMessageUndeleted` | Message identifiers and thread references only; no message body copied into audit data. |
| ARM writes | Administrative Activity Logs | Actual async terminal outcomes; reads are not writes. |
| Blob, File, Table, Queue operations | Corresponding `Storage*Logs` table and/or Blob diagnostic archive | Requires an enabled diagnostic category and current destination. Independent reads have independent source IDs. |
| Key Vault secret operations | `AZKVAuditLogs` and/or Blob diagnostic archive | Requires `AuditEvent`; secret values and backup material are excluded. |

File and Teams audit records use the existing Graph UAL, Exchange
`Search-UnifiedAuditLog`, and Management Activity pipelines. File events route to
`Audit.SharePoint`; Teams events route to `Audit.General`. Feed subscription,
availability delay, retention and collection permissions still apply. An action
does not appear in every collector.

Teams internal message sends and premium message deletions currently retain
native facts without publishing `MessageSent` or `MessageDeleted`: the Microsoft
documentation puts participant, preview and entitlement conditions on these
events. Graph selected-application site grants likewise retain facts without an
invented sharing event. This is a local coverage boundary, not a claim that
Microsoft never audits these operations.

New workload facts carry the internal mapping version `business-evidence-v1`.
Earlier facts keep their previous suppressed disposition during replay.
The version and local provenance are not added to Microsoft response payloads.
Mutation and source fact commit together; projection retries reuse deterministic
record IDs and the existing durable receipt.

The mapping sources include a commit-pinned
[Management Activity schema](https://learn.microsoft.com/office/office-365-management-api/office-365-management-activity-api-schema)
and a hash-verified snapshot of Microsoft's current
[audit activities reference](https://learn.microsoft.com/purview/audit-log-activities).
The latter's publishing Git repository is private. For `Members.Role`, the
structured Management Activity enum (Member 0, Owner 1, Guest 2) takes precedence
over differing role numbers in the activity-description prose.

## Failed data-plane operations

Authenticated Blob, File, Table, Queue and Key Vault failures can also reach the
configured diagnostic tables and archives. Capture begins only after local
credential verification and same-tenant account/vault ownership are established.
Permission denials, precondition failures and supported validation errors after
that point retain the real HTTP status and service error code. A Table changeset
failure retains its failed child operation while preserving the normal outer
HTTP 202 response and rolling back the changeset.

The failure source is written in a separate bounded transaction after rollback.
Its identity derives from the server request ID, so retrying capture cannot
duplicate delivery. Client correlation IDs do not grant idempotency authority.
The source contains the owning resource ID, verified principal ID when available,
operation, time, status, error code and authentication mode. It does not copy
headers, bodies, child paths, secret values, keys, tokens, SAS strings or exception
messages. Shared Key and SAS facts need not identify an individual principal.

Invalid credentials, foreign-tenant targets, unknown accounts/vaults, anonymous
requests and validation failures before authentication do not select a diagnostic
tenant. This also excludes network-policy rejection performed before credential
validation and malformed batch envelopes without an authenticated child operation.
These are deliberate attribution limits. If failure persistence itself fails,
the original public error is preserved and a sanitized request-ID warning is
written to the runtime log.

Failure rows expose `StatusCode`/`StatusText` in Storage tables and
`ResultType`/`ResultSignature` in Key Vault tables. Archives carry `resultType:
Failed`, the status signature and `level: Error`. Successful historical events
are not rewritten. Native lease events use `LeaseBlob`/`LeaseFile`; FileREST range
reads use the documented [List Ranges operation](https://learn.microsoft.com/rest/api/storageservices/list-ranges).

## Collect a connected tenant

The `living-small-business` and `living-enterprise` templates stack
`business-azure-operations` over the existing tenant and content profiles. That
layer enables all log categories for Blob, File, Table, Queue and Key Vault,
with both the generated workspace and storage account as destinations, before
the corresponding content writes. Existing simpler templates retain their
original single-service diagnostic recipe.

Run the native qualification after starting the API, worker and PostgreSQL:

```powershell
python -m tools.verify_living_tenant --config PATH_TO_NATIVE_CONFIG --phase evidence --report work/living-evidence.json
python -m pytest tests/acceptance/test_living_evidence_workflows.py -q
```

The verifier creates a fresh small tenant, performs permission-checked public
actions, waits for the existing projection jobs and availability timestamps,
and compares collected public IDs with a snapshot of committed native sources.
It does not replace absent records with imported evidence.

| Evidence | Collector used | Query boundary |
|---|---|---|
| Application sign-ins | Local Graph REST | `/beta/auditLogs/signIns` with the `servicePrincipal` event-type filter; v1.0's interactive-user view is not relabeled as app-only support |
| Directory and application credential changes | Original Microsoft Graph Python SDK | `/v1.0/auditLogs/directoryAudits`, including continuation pages |
| Inbox rules and file renames | Graph REST async UAL query | `/v1.0/security/auditLog/queries` and its records |
| The same Purview records | Original ExchangeOnlineManagement module | `Search-UnifiedAuditLog`, bounded session paging |
| The same subscribed records | Management Activity REST | Start subscriptions before actions, then list and retrieve content |
| ARM resource and provider registration changes | ARM REST | Subscription Activity Logs, preserving event IDs and provider targets |
| Successes and authenticated denials for all five data planes | Logs REST and original Azure Blob SDK | Dedicated tables and actual downloaded diagnostic archive bytes |

The JSON report includes source IDs, expected and observed record IDs/counts,
collector versions and explicit limitations. Credential material stays in the
owned runtime and process memory. The original Exchange module runs in the
existing pinned Linux client container on Windows; the server and worker stay
native. The client trusts the lab CA only inside its process.

Two additional checks update an imported file through the administrative fixture
API and advance only the evidence clock. The file remains readable without
creating a new native source or feed delivery. A normally issued token remains
valid, and a correctly signed, already expired local control token remains
rejected by the real HTTPS authentication boundary. The test does not alter the
host clock or token lifetime.

Provider registration projection accepts the subscription's provider identity
described by [Providers Register](https://learn.microsoft.com/rest/api/resources/providers/register?view=rest-resources-2021-04-01)
and emits the existing [Activity Log schema](https://learn.microsoft.com/azure/azure-monitor/platform/activity-log-schema).
This evidence-only parsing does not broaden normal ARM authorization scopes.
See the [qualification record](qualification/living-evidence.md) for the tested run.
