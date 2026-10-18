# Local binary evidence storage

This branch introduces shared PostgreSQL storage for mailbox content and later drive
and Teams evidence. Public workload APIs must authorize the containing resource before
reading any bytes. Content IDs alone are never public authorization.

## Upload and download

All management paths below require the separate local admin bearer token. They are
under `/_mock/tenants/{tenant_id}/content`.

| Method and suffix | Behavior |
| --- | --- |
| POST `/uploads` | Declare `size`, lowercase/uppercase SHA-256 `sha256`, and simple MIME `media_type`; returns `uploadId` and `chunkSize` |
| PUT `/uploads/{uploadId}/chunks/{ordinal}` | Supply binary bytes, starting at ordinal zero; one MiB per chunk except the final chunk |
| POST `/uploads/{uploadId}/complete` | Verify contiguous chunks, exact length and SHA-256 atomically; returns finalized `id`, `size`, `sha256`, `media_type` |
| GET `/objects/{id}/$value` | Stream finalized bytes; supports one HTTP byte range and returns 416 for invalid or multiple ranges |
| GET `/manifest` | Export finalized IDs, sizes, SHA-256 hashes and media types |
| POST `/validate` | Validate a manifest's `content` entries; optional `mapping` maps exported IDs to re-uploaded local IDs |
| POST `/reset` | Delete content only when no workload/history references remain; otherwise 409 |

The default object ceiling is 256 MiB, configured with `content_max_object_bytes`
(or `MOCKAZURE_CONTENT_MAX_OBJECT_BYTES`). Chunks are at most one MiB. Uploads may
arrive out of order. Repeating an ordinal with identical bytes is idempotent; conflicting
bytes return 409. Completion refuses missing or mismatched bytes and leaves the upload
unpublished. Empty content is finalized without chunks using SHA-256 of empty bytes.

Only finalized blobs may be referenced. Deduplication is tenant-local and includes
media type so a second upload cannot silently change another resource's Content-Type.
Completing an upload again returns the same canonical ID while its receipt and content
remain retained. No source URL is fetched, and content bytes are never regenerated.

## Export, restore and retention

Export the manifest and download its objects separately. To restore, upload the exact
bytes, complete each upload, and validate the exported manifest using the old-to-new ID
mapping. Then import workload records with those new IDs. Incorrect size, hash or media
type fails validation; cross-tenant references are rejected. The historical tenant
configuration export keeps its existing schema and does not claim to contain binary data.

Workload repositories pin each live or historical blob reference and release pins only
when the owning version can be removed. A cursor-retained version must retain its pin.
The shared maintenance cleanup service prunes unreferenced uploads/objects older than
24 hours within its existing deletion budget. Successful completion, including deduplication
and completion retries, renews the canonical object's24-hour grace period under its lock.
The internal `created_at` column records this retention age, not an evidence timestamp. Active downloads and reference creation
lock the object against deletion. Reset refuses referenced bytes rather than cascading
through mailbox, drive or Teams evidence. Migration downgrade likewise refuses data loss.
