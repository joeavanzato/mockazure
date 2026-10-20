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
