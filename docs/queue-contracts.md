# Queue service contract

Implemented profile: Azure Queue REST **2025-01-05**, **2026-02-06**, and **2026-04-06**, PostgreSQL-backed state,
path-style local endpoint `{queueOrigin}/{account}/{queue}`. The original Python
`azure-storage-queue==12.15.0` passes verified local HTTPS workflows with its
default raw text and explicit `TextBase64EncodePolicy`/`TextBase64DecodePolicy`.
Full Python/TypeScript/.NET/PowerShell phase D qualification remains pending D4.

The operation catalog bundles the official Azure REST Queue Swagger at revision
`ae348cc71f54a61f5d17472052cfef279a23db6b` plus hash-checked Microsoft Learn
references. Request and response shapes use the Queue XML contract, not Graph JSON.
No tenant endpoints are consulted by conformance tests.

| Operation | Local behavior | Boundaries |
| --- | --- | --- |
| List/Create/Delete Queue | Durable namespace and metadata; create returns 201, repeat with identical metadata 204, conflicting metadata 409; delete 204 | Delete completes immediately; Azure's possible asynchronous deletion window is not simulated |
| Get/Set Queue Metadata | Replacement metadata, headers and count of unexpired messages including invisible messages | Count is exact locally; Azure calls it approximate |
| Put Message | XML MessageText, message UUID, insertion/expiry, pop receipt and next-visible time; 201 | 64 KiB UTF-8 encoded text, no implicit Base64 decoding; default TTL seven days, positive TTL or -1 forever; visibility 0..seven days and smaller than finite TTL |
| Peek Messages | Visible unexpired messages, no receipt or visibility/count mutation | 1..32 messages; no visibilitytimeout option |
| Get Messages | Row-locked competing receivers; increment count, rotate receipt, hide until visibility ends; 200 | 1..32 messages; visibility 1..604800 seconds, default 30; expiry can precede the visibility deadline |
| Update Message | Optional text replacement, receipt rotation and visibility headers; 204 | Requires current receipt; visibility 0..604800 and no later than expiration |
| Delete Message | Delete with current receipt; 204 | Visibility expiry alone does not invalidate a receipt; redelivery/update does; stale receipt 400 PopReceiptMismatch; absent/expired message 404 |
| Clear Messages | Deletes visible and invisible messages; 204 | Requires delete authority, not just processing rights |

SharedKey/SharedKeyLite use the existing canonical request verifier. OAuth requires
Storage audience and current scoped Queue control Actions or message dataActions;
ARM management grants do not imply message access. Service SAS uses read/add/update/
process permissions and is bound to one queue. Account SAS additionally controls
service/container/object scope; Update Message specifically requires `u`, not `w`.
Key rotation, disabled shared-key access and account network policy take effect on
the next request. SAS time/IP/protocol constraints remain enforced.

Queue listing caps pages at 5,000. One-hour opaque markers bind account, tenant,
cloud generation and query. Import/reset invalidates continuations. Restart
preserves delivery state and current receipts. Receipts are stored as SHA-256
digests; explicit cloud snapshots preserve message text and delivery timing but
omit receipts and message IDs. Restore generates new message IDs and clears
receipt authority; it also rotates account keys through the existing lifecycle.
Operation facts contain names, authentication type and outcome, never message
payloads or pop receipts. Bounded cleanup removes expired messages and markers.

Unsupported operations fail explicitly: Queue ACL/stored access policies, Queue
service properties/stats, user delegation keys and delegation SAS. Unsupported
query/conditional options are rejected. Message XML supports UTF-8 text and rejects
DTD/entity declarations; bodies above the local 1 MiB XML envelope cap are rejected.
Finite TTL must fit Python's datetime range. Snapshot limits are 10,000 namespaces
and 100,000 messages per account. Queue state must be exported/reset before
downgrading migration 065. FIFO ordering and delivery throughput are not claimed;
G2 still owns load/scale measurements, including existing tenant transaction locks.

Verification: `tests/integration/test_queue_{workflows,authorization,lifecycle,sdk}.py`
and the offline Storage catalog/source closure check. See
[Queue REST](https://learn.microsoft.com/en-us/rest/api/storageservices/operations-on-queues),
[Get Messages](https://learn.microsoft.com/en-us/rest/api/storageservices/get-messages),
[Update Message](https://learn.microsoft.com/en-us/rest/api/storageservices/update-message),
and [account SAS](https://learn.microsoft.com/en-us/rest/api/storageservices/create-account-sas).

The original Az.Storage 8.1.0 module emits 2025-01-05; TypeScript Queue 12.32.0
emits 2026-04-06. The latter has its own pinned Swagger; its supported Queue
operation shapes match 2026-02-06. The pinned Azure repository publishes no
2025-01-05 Queue Swagger: that compatibility profile uses hash-bound official
version/operation documents with the shared structural schema, explicitly noted
in its catalog. User-delegation operations remain unsupported. Native phase-D
reports must identify each ecosystem's actual version; an SDK override is not
used to hide the module's request version.
