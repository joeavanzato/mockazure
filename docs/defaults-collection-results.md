# Defaults and collection evidence qualification

Runtime source: 5dc6dabaa12b886a8c03b406d53873757353c9c0.
Later changes in this PR correct the nullable test fixture, documentation and CI tooling.
This is a local development candidate; no package registry publication.

## Changes

The ordinary Compose profile now uses durable Linux PostgreSQL storage, the tested
readiness/migration/identity startup order and graceful shutdown. Existing complete legacy
clusters retain their bind storage and identity. Startup refuses absent or incomplete
storage when durable instance state proves a database previously existed.

49 historical milestone documents moved into the archive with original source hashes.
Archived JSON remains byte-identical. Active documentation links are valid.

Five Graph v1.0 security families now support typed atomic imports and permission-checked
queries backed by PostgreSQL: legacy alerts, modern alerts, incidents, Secure Scores and
Secure Score control profiles. Paging persists snapshot revisions and authorization binding.
The original collector inventory preserves all 4,136 named calls across 133 files, including
unmapped calls; 122 calls map to implemented profiles and still need parameter qualification.

## Verification

- 445 unit tests passed.
- All 418 current PostgreSQL integration cases passed across the initial broad run, resumed
  remaining/affected tests and final null-query regression run. The evidence report records
  every current node ID and test-source hash.
- Final default Compose smoke passed Docker-only initialization, HTTPS/schema/identity
  readiness, tenant creation, a full stack restart and persistence of tenant/instance bytes.
  An empty ephemeral database mount was refused; the original tenant remained available.
- The real Python Graph SDK 1.64.0 and PowerShell Graph Security 2.35.1 each retrieved
  301 legacy alerts, 301 modern alerts, five incidents, three scores and 101 control profiles.
  Typed user evidence and the separate modern-alert permission were checked.
- Unchanged pinned MES exported exactly 301 legacy alert IDs and 101 score recommendations
  with expected values. Original source hashes are recorded.
- Client containers used the API namespace on one internal Docker network. No real tenant
  traffic, host trust/DNS changes or collector source patches were used for this profile.
- Two wheel and source-archive builds were byte-reproducible and passed member inspection
  and clean native installation. All 311 runtime source hashes match the runtime commit.
- Ruff, relative Markdown links, archive hashes and preservation of existing CSDL types passed.
  The v1.0 schema adds 134 types, including 48 derived alert-evidence types.

The single independent whole-branch review found three Important issues: missing database
storage, null inequality and omitted inventory calls. All were reproduced and corrected in
one consolidated fix pass. The PostgreSQL regressions cover null/missing, equal and unequal
values for nullable strings, integers, decimals, booleans and 100 ns timestamps.

Initial failed attempts remain preserved. These included an obsolete capability-label test,
an invalid nullable enum fixture, resume-harness import-path and Windows spawn re-entry errors, and earlier local runtime
setup/client-harness failures. Passing reports are separate; no failed result was overwritten.

## Boundaries and next work

The pinned MES alert function has a Type/AuthType mismatch and selects the historical legacy
endpoint; modern alerts are qualified independently through both SDKs. The legacy endpoint is
kept for historical compatibility, without claiming upstream availability.

This profile does not qualify complete Hawk/MES workflows or every mapped parameter.
Collection version/cursor pruning, delete/reset and generic export integration remain pending.
No new million-record scale claim is made; earlier scale reports retain their original scope.

The next collection slice should address mailbox messages, attachments and raw content, then
device registeredUsers and the remaining Exchange settings/transport calls. SharePoint/OneDrive,
Azure provider diagnostics, Defender hunting and wider Purview coverage remain explicit gaps.
See [the supported contract](collection-evidence.md) and [default recovery rules](default-runtime.md).

Machine-readable evidence: [qualification record](defaults-collection-verification.json).

CI follow-up: pytest runs through the Python module entry point so repository helpers
remain importable. On POSIX hosts, the Docker bootstrap runs under the caller UID/GID;
private files retain mode 0600 and remain readable by the host verification process.
The initial Linux CI failures remain visible in the PR check history.
