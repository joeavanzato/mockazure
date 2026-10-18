# SharePoint / OneDrive evidence profile

The versioned source manifest is [site-drive.json](site-drive.json). Payload types
come from the pinned Microsoft Graph **v1.0** CSDL in the runtime schema catalog;
this is not a beta or whole-service claim. Source digests identify the reviewed
Microsoft Learn documents. Permission reference and role-action digests remain
in `src/mockazure/security/data/`.

## Public reads

| Microsoft Graph URI (prefix `/v1.0`) | Implemented behavior | Limits |
| --- | --- | --- |
| `/sites/{site-id}`, `/sites/root`, `/sites/{hostname}:/{path}` | Composite IDs, imported root facet, case-insensitive imported `webUrl` lookup | Ambiguous roots/paths return 404; URL is evidence, never fetched |
| `/sites?search={term}` | Authorized sites only, literal case-insensitive name substring; `*` lists authorized sites | No full-text search; Sites.Selected cannot enumerate/search |
| `/sites/{site}/lists[/{list}]` | Stored list metadata, snapshot pages | No list creation or schema editing |
| `/sites/{site}/lists/{list}/items[/{item}][/fields]` | Stored list items and open `fieldValueSet`; `$expand=fields` or `fields($select=...)` | Other expansions/filter/search unsupported |
| `/sites/{site}/drives`, `/drives/{drive}` | Canonical drive metadata | No drive creation |
| `/users/{id}/drives`, `/groups/{id}/drives`, their `/drive` aliases, `/me/drives`, `/me/drive`, `/sites/{site}/drive` | Explicit imported directory-to-drive edges, same canonical records and authority | Directory aliases require UUID (UPN unsupported); singular alias requires exactly one drive; `/me` delegated only |
| `/drives/{drive}/root`, `/items/{item}`, `/root:/{path}` | Canonical item IDs and case-insensitive root-relative path lookup | Item-relative paths unsupported; names cannot contain path separators |
| `/drives/{drive}/items/{item}/children` | Versioned child enumeration | Folder only; public metadata writes unsupported |
| `/drives/{drive}/items/{item}/permissions[/{permission}]` | Imported permission evidence | Display evidence does not grant site access; sharing writes unsupported |
| `/drives/{drive}/items/{item}/versions[/{version}]` | Imported version metadata | No version restore/delete |
| `/drives/{drive}/items/{item}/content`, `/versions/{version}/content` | 302 to local opaque capability; exact stored bytes, ranges and ETag | Only imported file content; no remote URL fetch/upload APIs |
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
and `tombstones`. A record has explicit `kind`, `id`, official `payload`, and
optional finalized tenant-local `content_id`. Edges have typed `source`, `relation`
and `target` keys. The import is atomic and limited to 1000 combined entries and
8 MiB. IDs/scopes are limited to 512 UTF-8 bytes; fields are bounded JSON, not code.
Binary content uses the existing chunked content upload/finalization API.

The canonical site ID equals scope. All drive items must belong to an acyclic,
unambiguous folder tree with one root per drive. Upserting a source replaces its
outgoing edges; omitted sources persist. Moves must replace the old and new parent
edge sets. Deleting a record removes incident edges; explicitly delete dependent
metadata as needed. `parentReference` and folder counts are imported evidence,
not automatically recomputed, so include coherent metadata in the same import.

GET `/export?scope=...` emits a restorable typed JSON import when within limits.
For larger stores, `format=ndjson` streams scope/record/edge rows under one snapshot
lease. Large restores need topologically ordered bounded import batches, retaining
each source's full outgoing edge set; raw NDJSON is not an import request.
POST `/reset` requires scope and expected_revision, clears metadata/grants, releases
version pins, and invalidates cursors. Export does not include authority or binary
bytes: retain finalized content and reapply explicit grants. Export/reset never
fetch remote evidence. Downgrade refuses to discard workload scopes, capabilities,
or new read-budget/fault state.

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
