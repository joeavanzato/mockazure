# SharePoint / OneDrive evidence profile

The versioned source manifest is [site-drive.json](site-drive.json). Payload types
come from the pinned Microsoft Graph **v1.0** CSDL in the runtime schema catalog;
this is not a beta or whole-service claim. Source digests identify the reviewed
Microsoft Learn documents. Permission reference and role-action digests remain
in `src/mockazure/security/data/`.

## Public reads

Native upload, rename/move, copy and delete are specified separately in the
[file workflow contract](../../docs/drive-native-workflows.md), with pinned
v1.0 operation sources in the living tenant inventory. Read semantics below apply
to both imported data and native results.

| Microsoft Graph URI (prefix `/v1.0`) | Implemented behavior | Limits |
| --- | --- | --- |
| `/sites/{site-id}`, `/sites/root`, `/sites/{hostname}:/{path}` | Composite IDs, imported root facet, case-insensitive imported `webUrl` lookup | Ambiguous roots/paths return 404; URL is evidence, never fetched |
| `/sites?search={term}` | Authorized sites only, literal case-insensitive name substring; `*` lists authorized sites | No full-text search; Sites.Selected cannot enumerate/search |
| `/sites/{site}/lists[/{list}]` | Stored list metadata, snapshot pages | No list creation or schema editing |
| `/sites/{site}/lists/{list}/items[/{item}][/fields]` | Stored list items and open `fieldValueSet`; `$expand=fields` or `fields($select=...)` | Other expansions/filter/search unsupported |
| `/sites/{site}/drives`, `/drives/{drive}` | Canonical drive metadata | No drive creation |
| `/users/{id}/drives`, `/groups/{id}/drives`, their `/drive` aliases, `/me/drives`, `/me/drive`, `/sites/{site}/drive` | Explicit imported directory-to-drive edges, same canonical records and authority | Directory aliases require UUID (UPN unsupported); singular alias requires exactly one drive; `/me` delegated only |
| `/drives/{drive}/root`, `/items/{item}`, `/root:/{path}` | Canonical item IDs and case-insensitive root-relative path lookup | Item-relative paths unsupported; names cannot contain path separators |
| `/drives/{drive}/items/{item}/children` | Versioned child enumeration | Folder only; native file creation uses the parent/name content PUT |
| `/drives/{drive}/items/{item}/permissions[/{permission}]` | Imported permission evidence | Display evidence does not grant site access; sharing writes unsupported |
| `/drives/{drive}/items/{item}/versions[/{version}]` | Imported version metadata | No version restore/delete |
| `/drives/{drive}/items/{item}/content`, `/versions/{version}/content` | 302 to local opaque capability; exact stored bytes, ranges and ETag | Imported and native file bytes; no remote URL fetch |
| `/drives/{drive}/root/delta`, `/items/{root-id}/delta` | Initial hierarchy, changed rows, deleted facet, durable next/delta links, `token=latest` and `delta(token='...')` | Root only, metadata import is mutation source; no arbitrary-folder delta or `$expand` |
| `/sites/{site}/permissions[/{permission}]` | GET authoritative application grants, POST create/upsert, DELETE revoke | Application identities only; no PATCH; imported permission display records are not returned here |

`/site/drive`, directory `/drive` and `/me/drive` tails use the canonical drive
adapter, including content and root delta. All new public workload adapters are
v1.0 only. Missing resources return 404; unsupported relationships/operations
fail explicitly rather than returning plausible empty evidence.

## Authority and bounded queries

Every operation verifies current identity and token authority. Site/list reads
accept Sites.Read.All and its implemented write/manage/full-control supersets.
Drive reads also accept Files.Read.All / Files.ReadWrite.All; delegated drive
reads accept Files.Read / Files.ReadWrite. **Sites.Selected requires an explicit
application grant on the site**, including delegated calls. Delegated reads also
require an explicit user ACL on that site. These ACLs are separate from imported
sharing evidence and do not infer access from group membership or item inheritance.

Site application-permission CRUD requires Sites.FullControl.All. Delegated CRUD
additionally requires current SharePoint Administrator or Global Administrator
role and the modeled user site ACL. Grant changes invalidate saved collection
cursors, requiring a restart. Site-permission mutations record immutable native
facts plus transactional outbox entries. They **do not generate Purview/UAL/feed
records**: an exact Microsoft operation mapping is not established by the reviewed
references. Admin `/workloads/sharepoint/facts` exposes these facts for tests.

Supported collection options: `$select`, `$top` (1..1000), and bounded `$orderby`
by id/name/lastModifiedDateTime. Ordering is a local extension where the individual
Microsoft operation does not document it. List-item fields are the sole expansion.
Unknown, duplicate and malformed options fail with 400. No `$filter`, `$count`,
`$search` or arbitrary nested expansion is implied. Pages stop at an 8 MiB budget
with an envelope reserve; paging keeps a revision snapshot and deterministic
ordering. An individual oversized projected record returns 413.

Opaque continuation tokens are hashed in PostgreSQL, expire after one hour, and
bind tenant, client, subject, token permission set and resource path. Restarting
the API/database preserves them. Revision history stays pinned to the oldest
revision still needed by active cursors; bounded maintenance prunes older history.
Scope reset changes generation so old cursors return 410 after restore.

Downloads use random 256-bit, hashed, five-minute capabilities. Their content pins
and verification state persist in PostgreSQL. A previously issued URL remains
usable after ACL revocation/reset until expiry; new issuance checks current access.
Range streaming holds a blob lease and uses one database connection. Never log or
publish active download tokens as permanent evidence links.

The shared read budget/fault system covers `sites` and `drives`; aliases retain
the existing `users`/`groups` route budget. JSON batch invokes the same adapters and
charges each subrequest. Existing batch restrictions on encoded path segments remain.

## Import, export, reset and restore

Admin namespace: `/_mock/tenants/{tenant}/workloads/sharepoint`.
POST `/import` accepts `scope`, optional `expected_revision`, `records`, `edges`,
and `tombstones`. A record has explicit `kind`, public `id`, official `payload`, and optional
finalized tenant-local `content_id`. An optional `key` is its stable storage identity
within the site scope; omitted keys default to `id` for compatibility. Use distinct
keys for repeated parent-relative public IDs (two lists can each contain item `1`,
and two files can each have version `1.0`). `payload.id` remains the public ID.
Edges and tombstones carry the same `kind`, public `id`, and optional `key`.
Child lookup resolves the public ID through its parent relation. A drive's item IDs
remain unique within that drive. Site and drive keys equal their canonical public IDs. The import is atomic and limited to 1000 combined entries and
8 MiB. IDs/keys/scopes are limited to 512 UTF-8 bytes; fields are bounded JSON, not code.
Binary content uses the existing chunked content upload/finalization API.

The canonical site ID equals scope. All drive items must belong to an acyclic,
unambiguous folder tree with one root per drive. Upserting a source replaces its
outgoing edges; omitted sources persist. Moves must replace the old and new parent
edge sets. Deleting a record removes incident edges; explicitly delete dependent
metadata as needed. `parentReference` and folder counts are imported evidence,
not automatically recomputed, so include coherent metadata in the same import.

GET `/export?scope=...` emits a restorable typed JSON import when within limits.
It includes a versioned manifest bound to tenant/workload/scope and finalized binary
size/SHA256/media-type descriptors. `format=ndjson` streams scope/content/record/edge
rows under one snapshot lease. `format=zip` streams a portable ZIP64 archive containing
`manifest.ndjson` and `content/{source-content-uuid}.bin` for every referenced object.
ZIP archives cap distinct binary entries at 10000; they do not buffer file bodies.

To restore an archive, verify its manifest and each file's SHA256, upload/finalize its
bytes through the content API, then submit the manifest plus records/edges to `/import`.
Restore validates tenant/workload/scope and resolves each original content UUID to
finalized local bytes by size, SHA256 and media type. Missing or mismatched content
fails before metadata commits. This works after the source blobs have been removed.
Preserve the scope header's manifest identity; set expected_revision to the target's
current revision (zero after reset). ZIP entries are not executed or fetched remotely.

Large restores need topologically ordered bounded import batches, retaining each
source's full outgoing edge set and each batch's needed content descriptors; raw
NDJSON/ZIP is not itself an `/import` request. Credentials and grants are excluded.
POST `/reset` requires scope and expected_revision, clears metadata/grants, releases
version pins, and invalidates cursors. Reapply explicit authority after restore.
Downgrade refuses to discard workload state or revert parent-scoped identities to
an incompatible model. The scoped-key migration preserves existing IDs and edges.

## Qualification

`tools.verify_site_drive` creates two local 101-file libraries, then runs original
pinned msgraph-sdk 1.64.0 and Microsoft Graph PowerShell Sites/Files 2.35.1: discover
site/drives, walk all pages, hash **every** downloaded file, read initial delta,
restart PostgreSQL/API, and resume saved delta for rename/deletion. Module archives
and extracted trees are verified before and after. Local transport binding is the
existing harness with opt-in one-hop redirects restricted to same-origin HTTPS
`/content/{64-hex-token}` URLs; generated command implementations remain unchanged.
The qualification asserts denial for external, downgraded, non-content and query-bearing redirects.

Focused tests cover selected/delegated authority, source-evidence separation,
permission mutations, paging/encoding, expiry/ranges, restart, export/reset, retention,
upgrade refusal and throttled batch. Native client success is recorded by the
qualification report/CI, not implied merely by the presence of the harness.
