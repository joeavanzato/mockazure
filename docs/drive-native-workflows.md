# Native file workflows

Populate a project site, drive and folders with the business content profile.
The following Graph v1.0 operations then mutate the same metadata and bytes that
collectors read:

| Operation | Supported local behavior |
| --- | --- |
| `PUT /drives/{drive}/items/{item}/content` | Replace file bytes, preserve the ID, and retain the previous bytes as a downloadable version. |
| `PUT /drives/{drive}/items/{parent}:/{name}:/content` | Create or replace a file under an existing folder; 201 for creation, 200 for replacement. |
| `PATCH /drives/{drive}/items/{item}` | Rename or move a file using `name` and `parentReference.id` within the same drive. |
| `POST /drives/{drive}/items/{item}/copy` | Copy the current file bytes to a new ID, optionally naming a same-drive destination folder. Return 202 with a persisted, caller-bound local monitor URL; that URL returns 303 to the completed item. |
| `DELETE /drives/{drive}/items/{item}` | Tombstone the file and close its current relationships. Existing snapshot/history retention remains effective. |
| `POST/DELETE /sites/{site}/permissions[/{permission}]` | Existing native selected-application access management. A write grant permits a `Sites.Selected` application to mutate files; revocation denies subsequent access. |

Use the `/v1.0` prefix. Writes require documented Graph write scopes, an active
identity, and current write-level user/app site access where applicable.
Imported permission evidence never authorizes a write. `If-Match` rejects stale
item ETags with 412. Duplicate destination names return 409. Each successful
operation retains a native source fact without file bytes.

This is a bounded file workflow: maximum simple upload is 8 MiB; no upload
sessions, folder writes, cross-drive move/copy, conflict auto-rename, copied
version history, link creation or user invitation. Copying a bounded single file
finishes in the acceptance transaction, so the monitor immediately reports
completion; there is no simulated delay. The monitor expires after one hour.
Copy results have independent IDs and metadata. Byte storage uses the normal
tenant content store and its retention pins.

The authoritative Graph request and permission documents are pinned in
`src/mockazure/living_tenant/data/operations.json`. Existing read routes,
versions, content redirects and drive-root delta expose the resulting state.
