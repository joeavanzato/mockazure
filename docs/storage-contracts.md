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
| Blob content read/list, write, delete | `Microsoft.Storage/storageAccounts/blobServices/containers/blobs/{read\|write\|delete}` | DataActions; domain authorization prepared for C2 |
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
C1 has no snapshot creation, so this currently enumerates its live shares only.

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

Blob bytes/blocks/ranges/leases/snapshots and FileREST content are C2/C3 work.
Until those routes land, content SAS and Files OAuth tests exercise the real
authorization domain only, and do not claim an end-to-end content round trip.
Queues, Tables and Key Vault content remain subsequent phase D work.

C1 was verified against all four pinned client ecosystems, including a new local
account and cross-language reads of every created namespace. The core regression
gate passed 57 tests; the subsequent policy/managed-identity gate passed 15.
The broader run reported 1,289 passing tests, one skipped test, nine missing/stale
release-artifact gates, and one native test port collision (fixed and rerun).
Final release-artifact qualification remains part of G3. Blob/File content
qualification is still pending C2-C4.
