# Storage data-plane contract

The C1 namespace profile uses PostgreSQL account state, containers, shares,
stored access policies and encrypted delegation keys. The account keys and
settings are the same authority exposed through the ARM Storage provider.
This is a selected REST implementation; no SMB or NFS server is provided.

## Sources and versions

The operation catalog in `integrations/contracts/cloud/storage.json` mirrors
the packaged runtime catalog. Microsoft Azure REST specifications are pinned to
`ae348cc71f54a61f5d17472052cfef279a23db6b`, with complete source files and hashes.
Namespace requests support `2019-07-07` (Az.Storage legacy cmdlets),
`2025-01-05` (its newer bundled libraries) and
`2025-11-05` (the other pinned clients). The SAS authorization format and Blob
user delegation key profile are `2025-11-05`. Other versions reject explicitly. Anonymous public Blob requests may omit the
version; this local profile defaults them to `2025-11-05`. The legacy namespace
profile is cross-checked against the versioned Learn protocol references; no
2019 Swagger snapshot is present in the pinned Azure REST specification tree.

Official Learn protocol pages supplement Swagger for Shared Key canonicalization,
SAS formats, authorization and GET/HEAD aliases. Content hashes, retrieval dates,
and the source commit URL embedded by Microsoft are recorded in
`src/mockazure/cloud/data/storage/protocol-references.json`. The underlying
MicrosoftDocs repository returned 404 during acquisition; the committed Learn
snapshots support offline verification without inventing a repository revision.

## Authorization

| Operation | OAuth permission | Permission category |
| --- | --- | --- |
| List containers; container properties/metadata/ACL | `Microsoft.Storage/storageAccounts/blobServices/containers/read` | Actions |
| Create container; set metadata | `Microsoft.Storage/storageAccounts/blobServices/containers/write` | Actions |
| Delete container | `Microsoft.Storage/storageAccounts/blobServices/containers/delete` | Actions |
| Set container ACL | `Microsoft.Storage/storageAccounts/blobServices/containers/setAcl/action` | Actions |
| Get user delegation key | `Microsoft.Storage/storageAccounts/blobServices/generateUserDelegationKey/action` | Actions, account or ancestor scope |
| Blob content read/list, write, delete | `Microsoft.Storage/storageAccounts/blobServices/containers/blobs/{read\|write\|delete}` | DataActions on the selected C2 Blob routes |
| File content read/list, write, delete | `Microsoft.Storage/storageAccounts/fileServices/fileshares/files/{read\|write\|delete}` plus the corresponding `readFileBackupSemantics/action` or `writeFileBackupSemantics/action` | DataActions; `x-ms-file-request-intent: backup`; domain authorization prepared for C3 |

FileREST share management in this profile uses Shared Key or account SAS;
blob roles do not authorize File content. Control permissions such as Contributor
can authorize documented container management but do not grant blob-byte access.
Identities, inherited grants, account flags and the observed client network are
checked for each request. Forwarded client-IP headers are not trusted.

Shared Key and Shared Key Lite preserve encoded URI paths, fold canonical headers,
and sort query keys and values. Signatures use current account keys and a 15-minute
request-date window. Account SAS restricts service, resource level, operation,
time, protocol and optional IP range. Service SAS and Blob user delegation SAS
restrict content paths and permissions; they do not authorize namespace creation
or account-wide lists. Stored policy replacement and deletion take effect locally
without an eventual-consistency delay. SAS access never creates an OAuth principal.
Delegation SAS additionally intersects the issuing identity's current data grants.
User-bound delegation SAS, HNS directory SAS and encryption scopes reject.

Public Blob access requires both the account flag and a matching container ACL.
Public container access never permits listing the account's containers.

## Local endpoints and state

Use the exact service endpoints returned by the ARM account response. The local
development addressing form is `https://localhost:<service-port>/{account}/...`.
Core, Blob, File, Queue, Table and Key Vault have distinct listeners. Compose's
TCP ingress preserves the destination service port and TLS terminates in the
application. `mockazure init --compose` upgrades old generated listener metadata
without rotating the instance CA or identity. Updating an existing Compose lab
also requires the current `deploy/haproxy.cfg` and application image.

`cloud/storageState` and the combined cloud-state export include typed namespace
metadata and stored policies. They omit account and delegation secrets. Restore
preserves namespace state, rotates account keys and revokes delegated keys. Reset
and account deletion cascade to child namespaces and credentials. Maintenance
prunes expired delegation keys with a bounded batch. Migration 061 refuses a
downgrade that would discard live namespace or delegation state.

Mutations emit account-bound service events in the same PostgreSQL transaction.
Unsupported protocol, encryption, quota/performance and other behavior-bearing
headers reject rather than returning success without applying the request.
List pages use ordered names and `NextMarker`, with a maximum of 5,000 entries;
the local marker is a continuation key, not an Azure opaque token.
The Files list accepts metadata and snapshot inclusion as used by Az.Storage;
C3 adds immutable share snapshots and includes them in this listing when requested.

## Qualification and remaining work

Run `uv run --group clients python -m tools.verify_storage_namespaces` for the
four original client ecosystems. Each must create/list a container and share,
and the original Python clients read every namespace written by all four.
Instance CA trust is limited to the client process or isolated PowerShell
container. No SDK operation implementation is replaced.
Az.Storage uses explicit `https://127.0.0.1:<port>/{account}/` endpoints in its
container profile: its Files SDK recognizes IP hosts for emulator path parsing,
while `localhost` causes the returned share client name to be the account name.
The generated lab certificate already covers both loopback identities.

C1 unit/integration tests exercise Shared Key, SAS, current OAuth grants,
File backup intent, key rotation, network policy, stored policy revocation,
restore invalidation and listener isolation. Native-client results are recorded
separately; adding a runner is not proof of a passing qualification.

C2 adds Blob bytes, block commits, ranges, leases, snapshots and retention.
C3 adds FileREST content and HTTP OAuth backup-intent tests; native byte
qualification is recorded separately below. The [Table profile](table-contracts.md)
adds D1 entity workflows; the [Queue profile](queue-contracts.md) adds D2 delivery
workflows. The [Key Vault profile](keyvault-contracts.md) adds D3 encrypted secret
versions; recovery and full phase D qualification remain pending D4.

C1 was verified against all four pinned client ecosystems, including a new local
account and cross-language reads of every created namespace. The core regression
gate passed 57 tests; the subsequent policy/managed-identity gate passed 15.
The broader run reported 1,289 passing tests, one skipped test, nine missing/stale
release-artifact gates, and one native test port collision (fixed and rerun).
Final release-artifact qualification remains part of G3. Four-ecosystem binary
qualification passed C4 as recorded below.


## Blob content profile (C2)

The main Blob content profile uses `2025-11-05`; selected older compatibility
operations are documented in the C4 section below. This profile stores bytes
in the existing immutable PostgreSQL content chunks and streams reads/writes.
The local maximum is 256 MiB per object, with 1 MiB storage chunks. It supports
BlockBlob upload, download, HEAD, metadata/properties, block staging/commit/list,
single explicit byte ranges, MD5 validation, ETag/time conditions, and Blob
lease acquire/renew/release/change/break. Suffix/multiple ranges, CRC64, customer
encryption headers, tags, tiers, page/append blobs and copy-from-URL reject.

Account SAS works on byte operations as well as namespaces. Create-only SAS can
create a new Blob but cannot overwrite or mutate existing content; the pinned
2025-11 profile requires write permission for Put Block and Put Block List.
Service/user-delegation SAS signed download response overrides are applied after
signature validation. Snapshot/version-specific SAS resources remain outside
this profile. Current OAuth authority and account network/key settings are
checked on every operation. A lease is checked before cache validators.

Snapshots retain immutable bytes and do not inherit the base Blob's lease.
Versioning and soft-delete retention are opt-in account service settings in
the typed `/_mock/tenants/{tenant}/cloud/storageState` document:

```json
{"services":{"blob":{"isVersioningEnabled":true,"deleteRetentionPolicy":{"enabled":true,"days":7}}}}
```

This fragment belongs to an exported account entry, not the entire PUT body.
The corresponding ARM service-property writes are still unsupported. Retention
is 1–365 days when enabled; permanent-delete APIs are unsupported. Blob listing
supports metadata, snapshots, versions and deleted objects, with flat/hierarchy
prefix/delimiter behavior. Continuations are opaque, expire after one hour and
are bound to query/account/container/reset generation. This differs from the
C1 namespace list's name marker.

Typed exports include Blob versions, staged blocks and immutable content
references. They omit SAS secrets, account keys, leases and continuations.
Restore validates byte hashes, sizes and ownership before mutation, rotates
account keys and invalidates old uploads. Content payload transfer uses the
existing content export/restore system. Migration 062 preserves namespace state
and refuses downgrades with live Blob/staging state. Bounded maintenance prunes
expired continuations, seven-day staged blocks and expired soft-deleted content.

`python -m tools.verify_blob_content` runs the four original namespace clients
then the original Python Blob SDK through actual local HTTPS for byte/block,
range, snapshot, lease, conditional-write, paging and deletion interactions.
The final C4 gate below adds the 256 MiB restart/restore and four-ecosystem binary
qualification to this earlier C2 result.

C2 verification: 87 affected unit/integration tests passed (one explicit slow-content
skip), including preceding-head upgrade preservation and downgrade refusal. The
original Python 12.27.0 Blob SDK passed a 2,340,000-byte round trip and the stated
workflow; all four original namespace clients and cross-language reads passed.
Report source fingerprint: `823d68cbda15298420221eebf9be5308027127c51be2dd5351be8e7fd82ec595`.


## Files content profile (C3)

The main FileREST content profile uses `2025-11-05` (selected C4 compatibility
operations are listed below). It supports
nested directories, file creation/read/delete, properties and metadata, range
write/clear/read/list, directory paging and share snapshots. Files are limited
to 256 MiB, range updates to 4 MiB, pages to 5,000 entries and snapshots to 200
per share. Snapshots retain independent immutable content references. Snapshot
reads, listing and deletion are supported; writes into snapshots reject.

Paths are case preserving and case insensitive, with the documented component,
path, reserved-name and trailing-dot rules. XML-invalid filename characters
are percent encoded in directory listings. Empty-directory deletion succeeds;
nonempty deletion rejects. Custom SDDL/permission keys, rename, URL copy, handles,
NFS properties and SMB/NFS transports remain unsupported. The permission profile
uses inherited defaults; it does not simulate SMB ACL evaluation.

Shared Key, account/service SAS and current OAuth data grants protect content.
OAuth requires `x-ms-file-request-intent: backup` and the appropriate privileged
backup semantics. Share snapshot/create/delete operations in this profile use
Shared Key or account SAS; service SAS is limited to content operations. Infinite file leases support acquire/change/release/break, with stale
lease writes rejected. Blob-style HTTP `If-*` headers are not FileREST conditions
in this profile and reject. ReadOnly attributes protect byte writes/deletion.

Range clear zeros every requested byte, but deallocates only complete 512-byte
sectors. File properties follow replacement semantics for the HTTP property
group. Byte revisions use immutable PostgreSQL content chunks. Current range
writes stream the full replacement revision in bounded 1 MiB chunks: memory is
bounded but repeated small writes cause write amplification. C4/G2 qualify
large-object durability and performance; no high-throughput claim is implied.

Typed storage-state export includes live/snapshot trees, properties, ranges and
verified content references, omitting lease IDs, keys and continuations. Restore
checks byte ownership/hash/size before mutation, rotates account keys and clears
old lease/cursor authority. Deletion releases content pins; maintenance removes
expired cursors. Migration 063 preserves prior Storage state and refuses to
remove live Files state on downgrade. Content bytes use the existing content
export/restore mechanism.

`python -m tools.verify_file_content` runs the four original namespace clients,
the original Python Blob workflow and the original Python Files byte/snapshot/
lease workflow over local HTTPS. Native results are recorded after execution.
Four-ecosystem binary qualification and 256 MiB restart/restore passed the C4 gate below.

C3 native verification: original Python Files SDK 12.23.0 passed a 1,710,000-byte
workflow; all four namespace clients/cross-language reads and the Python Blob
workflow also passed. Source fingerprint:
`b9a65423295050a8f6b59e37d21353887ff26817572716b8084cded8a15b0eb9`.
The SDK requires aligned clear ranges locally, so its run clears 1,024 aligned
bytes; direct REST regression tests separately cover unaligned zeroing/allocation.


## Native compatibility and durability qualification (C4)

Original Az.Storage 8.1.0 uses both legacy and modern libraries within one
command: Blob/File writes and downloads request `2019-07-07`, while follow-up
properties/directory lookups request `2025-01-05`. The compatibility profile
supports Shared Key/Lite only. Blob 2019 supports live BlockBlob upload/read,
blocks, metadata/properties, basic metadata listing and delete. Files 2019
supports basic file/directory CRUD and properties, ranges and range listing.
Both services support selected 2025-01 read/HEAD/metadata lookups. Advanced
snapshot/version/lease flows and Files directory listing remain 2025-11-only
in this compatibility profile. Unsupported combinations reject explicitly.

The legacy FileREST profile requires explicit creation/last-write timestamps,
attributes and inherited/preserved permission headers, as its contract requires.
It rejects newer backup-intent/trailing-dot/change-time headers. Original SDK
keyword casing (`File`, `Now`, `Inherit`, `Update`) is accepted. OAuth and SAS
are qualified against the main 2025-11 profile, not inferred for these older
compatibility operations. Exact operations and source references are recorded
in the operation catalog's `compatibleVersions` entries.

The .NET SDK's nested client URI builders require loopback-IP endpoints for
account-prefixed emulator addressing. Qualification explicitly registers those
origins in the local runtime and uses its CA; Host enforcement remains strict.
`localhost` can work for initial namespace creation yet lose the namespace in
nested Blob/File client URIs. PowerShell uses the same IP convention inside
its isolated container. No SDK operation implementation is replaced.

The phase C runner is `python -m tools.verify_cloud_workflows --phase C
--ecosystems python powershell dotnet typescript --report work/cloud/C.json`.
It runs the original namespace/workflow cohort and binary workflows, writes
256 MiB through the Python Blob and Files SDKs and 3 MiB + 17 bytes through
the other ecosystems, restarts API/worker, restores cloud metadata, checks old
keys are rejected, and verifies hashes again with all clients plus independent
Python cross-language reads. The gate records exact source fingerprints and
client versions; a prepared runner alone is not a qualification pass.

The integration durability gate streams 256 MiB Blob and File content, opens a
new database pool, restores typed state and checks metadata/hashes with a
32 MiB Python heap ceiling. Reset invalidates staging and continuation authority.
Storage source events include account/resource name, operation, authentication
mode and outcome without key/SAS values. Replaying a committed event identity
is idempotent; a new valid HTTP mutation remains a distinct action. F3/F4 add
routing of these internal facts into public diagnostic destinations.

Original Python SDK uploads carry `x-ms-meta-*` headers on staged blocks. These
are tolerated without changing published Blob metadata; metadata applies on
Put Block List. The 2025-01 key-authorized share-delete namespace operation
remains available independently of the content read compatibility profile.


Request bodies are received into temporary storage before publication locks are
acquired: a 1 MiB in-memory spool threshold, 256 MiB body cap, and 300-second
reception deadline are local emulator limits (413/408 on excess). Temporary bytes
carry no publication authority. Authorization and lease time are refreshed after
reception/lock waits; failed publication leaves no successful source event.
Downloads retain a read lock on the selected immutable content object while
releasing account/tenant mutation locks before sending bytes. This permits an
independent request during a paused upload or download; it does not claim a
particular throughput level for database-side mutations.

Put Blob discards abandoned staged blocks transactionally. Uncommitted blocks
expire after seven days without successful staging activity on that blob, not
seven days after each individual block. Historical block-list requests return
404 for missing selectors and never expose live uncommitted blocks. Reacquiring
an active lease with its matching proposed ID succeeds; omitted break period on
a finite lease preserves its remaining duration.

The native Python 256 MiB workflow has a one-hour execution budget because the
current Files range implementation rewrites each complete immutable revision.
The other client workflows retain a 30-minute budget. These are test harness
budgets, not throughput guarantees; the report records actual elapsed time.


### Final C4 qualification evidence

Runtime commit `39f68e7f5e6c3a173115e2c38973ee3b5e9a5ebc`, fingerprint
`f40b76d4d552737c285456bf95e257ffd37f4851f1765869f175fa4b83d6192e`,
passed the full original-client matrix in 1,488 seconds. Python stored 256 MiB
for each service; TypeScript, .NET and PowerShell stored 3,145,745 bytes each.
All four ecosystems passed reads after API/worker restart and cloud restore;
old keys were rejected, restored keys worked, and cross-client hashes matched.
The reviewed regression suite passed 106 tests, including 15 review cases and
a separate 256 MiB restore with Python peak memory of 6,145,240 bytes.
See [the recorded report](qualification/cloud-phase-c.json). Operation limits and
File write amplification remain. GitHub CI is not included in this local pass claim.
