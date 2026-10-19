# Key Vault Secrets contract

D3/D4 implement secret versions, lists, recovery, purge and explicit encrypted backups against the
official **2025-07-01** data-plane schema. Vault management remains **2025-05-01**.
Sources are bundled at Azure REST revision
`ae348cc71f54a61f5d17472052cfef279a23db6b` with hash-checked Microsoft Learn references.
Request and response fields are checked against the cached definitions; writable
requests reject unknown and read-only fields. Runtime validation is offline.

| Microsoft URI | Supported operations | Behavior and limits |
| --- | --- | --- |
| `/secrets/{name}` | PUT set; GET/PATCH latest aliases | PUT returns 200 and creates a distinct version. Names are case-insensitive, 1..127 alphanumeric/hyphen characters; values <=25 KiB UTF-8 |
| `/secrets/{name}/{version}` | GET, PATCH | Exact version lookup; PATCH changes attributes/tags/content type, not value; update response never discloses a preexisting value to a set-only caller |
| `/secrets` | GET | Metadata only, no secret values; maxresults 1..25; opaque continuations |
| `/secrets/{name}/versions` | GET | Version metadata, no values; current list permission checked on each page |
| `/secrets/{name}` | DELETE | HTTP200 metadata, no value; retains all versions for vault retention (7..90 days) |
| `/deletedsecrets`, `/deletedsecrets/{name}` | GET; DELETE named secret | Metadata lists/get; purge HTTP204 only if protection permits; current permission checked on each page |
| `/deletedsecrets/{name}/recover` | POST | HTTP200, all versions/IDs preserved; no implicit value disclosure |
| `/secrets/{name}/backup`, `/secrets/restore` | POST | Explicit encrypted backup/all-version restore; separate operator key and reference; same tenant/subscription/geography; active/deleted name conflict HTTP409 |
| ARM `/subscriptions/{id}/providers/Microsoft.KeyVault/deletedVaults` and `/locations/{location}/deletedVaults/{name}` | GET list/get; POST `/{name}/purge` | Original 2025-05-01 schema; current control-plane permissions; purge returns HTTP200 synchronously and rejects protected vaults |
| ARM vault PUT with `properties.createMode="recover"` | Recover | Same original scope/location/configuration and encrypted contents; scoped RBAC assignments are not restored |

Get rejects a disabled version and does not silently choose an older enabled
version. Microsoft explicitly allows GET outside `nbf`/`exp` for testing/recovery;
those timestamps are preserved and returned, without inventing a read-time deny.
Content type is limited to 255 characters; at most 15 tags with keys/values up to
512 characters. Certificates, keys, managed-secret rotation and certificate format
conversion (`outContentType`) are outside this profile. JSON request envelope cap
is 192 KiB; unsupported conditional/query options fail explicitly.

Authorization uses the `https://vault.azure.net` token audience and current
identity state. A missing/invalid/wrong-audience bearer receives a local 401
challenge; the original SDK's empty discovery request is challenged before body
parsing. ARM management grants do not imply secret data access. Current vault mode
selects scoped Key Vault RBAC dataActions or tenant-owned access policies, including
transitive security-group grants. Switching modes immediately stops using the old
mode. Current public-network/IP rules apply; forwarded headers do not grant access.

Secret values use the existing instance wrapping key with authenticated encryption
bound to tenant, vault, name and version. Substituted ciphertext fails closed.
Successful operation facts contain operation/name/outcome, never values. Default
cloud snapshots contain metadata/public versions only, with no plaintext or
ciphertext. Restoring such a snapshot preserves metadata and IDs but clears value
material; GET returns explicit local 409 `SecretValueUnavailable`. Explicit encrypted
backups recover values. Restart preserves encrypted values.
Snapshots cap secrets at 10,000, versions at 1,000 per secret/100,000 total.
Import/reset clears cursors; one-hour continuations bind vault, tenant, generation,
query and list kind. Downgrade 067 requires exporting/resetting secret state first.
Deleted vault snapshots include safe configuration/timeline metadata. ARM vault or
resource-group deletion retains encrypted secret rows until recovery or permitted purge.
Maintenance removes expired secret/vault tombstones, even with purge protection,
after the configured retention period.

Configure `keyvault_backup_key_file` and `keyvault_backup_key_ref` explicitly for
backups; the file holds a separate Fernet key and resolves relative to the config
file. Keep it outside Git. Missing configuration returns local HTTP409
`BackupKeyRequired`. Backups contain a versioned encrypted manifest and all secret
versions (maximum 500); restores check tenant, subscription, geography and key
reference before writing. This envelope is a MockAzure format, not interoperable
with Azure backup blobs. The two supported vault regions share the United States
geography. Restore has a 32 MiB envelope cap; normal requests retain 192 KiB.
Recover uses the retained configuration; edit it after recovery with PATCH.

## Local addressing and original SDK compatibility

Native IDs must have exactly `/secrets/name/version` after the origin. The earlier
management-only path origin cannot appear inside IDs: the original Microsoft SDK
rejects an extra vault path segment. MockAzure now emits a vault-specific local
origin, for example
`https://local-vault.127-0-0-1.mockazure.test:8448/secrets/name/version` when the
configured Key Vault origin is `https://127.0.0.1:8448`. The hostname is derived only
from validated local configuration and a validated vault name.

Both native virtual-host routes and the existing DNS-free request alias
`https://127.0.0.1:8448/local-vault/secrets/name/version` resolve to the same vault.
ARM `vaultUri` retains that local alias. Pagination retains the caller's route/origin
so ordinary original-SDK workflows using the alias require no DNS modification.
To dereference native IDs directly outside an ASGI test client, configure local DNS
or hosts entries for the emitted hostname and enroll that exact name in the
instance's allowed TLS hosts; renew the leaf certificate explicitly if needed.
MockAzure does not edit the computer's hosts file or contact Microsoft tenants.

The original Python `azure-keyvault-secrets==4.11.3` passes local CA-verified HTTPS
using the alias and `api_version="2025-07-01"`. The local endpoint adapter supplies
a valid local token and sets the SDK's documented `verify_challenge_resource=False`
because the loopback origin differs from the Microsoft resource audience. TLS
verification stays enabled, token scope is checked, server audience enforcement
stays enabled, and the harness rejects requests escaping the configured origin.
No SDK/parser/protocol replacement is used. Four-ecosystem phase D qualification
remains pending D4; no throughput claim is made before G2 measurements.

See [Set Secret](https://learn.microsoft.com/en-us/rest/api/keyvault/secrets/set-secret/set-secret?view=rest-keyvault-secrets-2025-07-01),
[Get Secret](https://learn.microsoft.com/en-us/rest/api/keyvault/secrets/get-secret/get-secret?view=rest-keyvault-secrets-2025-07-01),
[secret attributes](https://learn.microsoft.com/en-us/azure/key-vault/secrets/about-secrets),
and [Key Vault RBAC](https://learn.microsoft.com/en-us/azure/key-vault/general/rbac-guide).


The original Az.KeyVault 6.6.1 secret cmdlets use REST 7.0. That profile is bound to
the actual cached `Legacy/stable/7.0/keyvault.json`, including inherited attributes
and writable/read-only rules. Responses omit the later `recoverableDays` field;
paging preserves and binds the selected API version. Keys and certificates are
still outside this secret profile.

Az's `VaultUriHelper` always builds `https://{vault}.{dnsSuffix}` on port 443.
The qualification harness supplies its configured local DNS suffix, an isolated
container hosts mapping, and a loopback-only TCP passthrough from 443 to the
dedicated 8448 listener in the same isolated Docker network namespace. TLS remains
end-to-end and verified against the instance CA. This changes no host-computer DNS,
SDK code, command implementation, signatures or API payloads. The temporary port
adapter is removed after each PowerShell run. Explicit encrypted backups accept
both padded and unpadded base64url wire encoding, with the same authenticated
manifest checks.

The local control API's authoritative `cloud/resourceState` replacement removes secret rows and their versions when it removes an active vault, preserving its original fixture-cleanup behavior. It does not run the public ARM soft-delete workflow. Recreating that scope therefore cannot inherit removed values. Public ARM deletion continues to retain recoverable vault/secret state under the documented retention rules. The combined cloud snapshot remains the supported way to move coherent resource and secret metadata state.
