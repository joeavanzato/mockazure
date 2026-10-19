# Azure Table service profile

The Table listener uses a separate local HTTPS origin with `/{account}` path addressing. The selected REST API is `2019-02-02`, independently pinned from Blob and Files. Its source is the Azure Tables Swagger at revision `ae348cc71f54a61f5d17472052cfef279a23db6b` plus hash-bound Microsoft Storage protocol documents. The Swagger lives under `cosmos-db/data-plane/Tables`; this implementation models Azure Storage accounts, not Cosmos DB.

Implemented operations are create/list/delete Table, insert/read/query/delete entity, conditional merge/replace, unconditional upsert, and one atomic multipart changeset. Queries support typed comparisons (`eq/ne/gt/ge/lt/le`), `and/or/not`, grouping, `$select`, `$top`, and opaque continuation headers. Unsupported functions/options reject explicitly. There is no Atom, Cosmos behavior, Table ACL/service-properties/stats endpoint, or read-only/multiple-changeset batch profile.

Custom properties retain their EDM type. Int64 values use decimal JSON strings with annotations; binary, Guid and DateTime also retain their type. DateTime preserves seven fractional digits. Null properties are omitted, including merge patches. `Timestamp` is server-managed, and the ETag derives from that timestamp so original SDKs can reconstruct it under minimal metadata. JSON no/minimal/full metadata modes are distinct.

Microsoft limits enforced include 252 custom properties, 64 KiB UTF-16 strings/binary properties, 1,024-character keys, 100 distinct entities in one partition per transaction, 4 MiB batch bodies, 1,000 entities/page and 15 filter comparisons. Local bounds additionally include a 1 MiB entity/request representation, an 8 KiB filter, 32 nested expressions, a one-hour continuation lifetime, and 100,000 entities per table in a cloud snapshot. Table names are case-insensitive and preserve creation spelling. Keys and string comparisons use deterministic binary ordering; arbitrary query functions and locale-sensitive collation are not emulated.

Shared Key/Lite uses Table-specific canonicalization. Current Storage OAuth data grants and account/network policies are enforced on every request. Control-plane Contributor rights do not grant entity access. Entity write RBAC accepts the documented `write` alternative or the specific add/update combination. Batch members are authorized individually with their own conditional-update mode while the unchanged outer HTTP request supplies the signature. Ad-hoc Table SAS enforces its table name, permission set, expiry, protocol/IP and inclusive partition/row bounds. Stored-policy Table SAS and user-delegation Table SAS are not implemented.

Table state participates in typed cloud export/import/reset. Invalid input is rejected before replacement; imports rotate account keys, allocate fresh internal identities and discard continuation authority. Public mutations append service facts atomically without copying property values into facts. Import is not a public entity-write event. Expired continuation records are pruned by cloud maintenance. Migration 064 refuses a downgrade that would discard Table state.

Validation is recorded in `tests/unit/test_table_edm.py` and the `test_table_*` integration suites. Original-client Python checks use `azure-data-tables==12.7.0` over a local CA-verified HTTPS listener. The [phase D report](qualification/cloud-phase-d.json) qualifies Python, PowerShell, .NET and TypeScript workflows at runtime `589d741`, including restart, cloud restore, key rotation and cross-language reads. PowerShell entity CRUD is explicitly labeled REST fallback.


Original Az.Storage 8.1.0 uses Microsoft.Azure.Cosmos.Table 1.0.8 with
`x-ms-version: 2017-07-29`. A bounded legacy profile supports namespace
create/list/delete, including its `POST /Tables()` alias. Entity calls still use
2019-02-02; no legacy SAS or Cosmos behavior is claimed. The catalog distinguishes
the official version/operation documentation from the newer shared structural
Swagger. The PowerShell matrix uses an explicit REST fallback for entity CRUD
because Az.Storage has no entity cmdlets. Batch `$format` selects the actual JSON
metadata mode; omitted Content-ID stays omitted, and supplied IDs are echoed.

Account SAS follows the [operation-specific permission table](https://learn.microsoft.com/en-us/rest/api/storageservices/create-account-sas): Query Tables requires container resource type `c` and list permission `l`; unconditional replace/merge requires both `a` and `u`. Conditional updates require `u`. Each batch member applies its own `If-Match` rule. Regression coverage includes denial without add authority and the distinct service scope used by Queue listing.
