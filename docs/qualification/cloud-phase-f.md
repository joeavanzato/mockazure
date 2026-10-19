# Query and diagnostic workflow qualification

Runtime commit: `0a242c0b38ad7e349ea8eb51b90a9f67bd969826`. Source fingerprint: `4630c3f56ad20f144899eec8b24bcb18e44a33f385038a080b9f6d32c414ccef`.
The [machine-readable report](cloud-phase-f.json) passed in 251.172 seconds against a fresh local Docker lab built from that committed source.

Original Python, PowerShell, .NET and JavaScript clients created/read diagnostic settings, paged Resource Graph inventory, and queried durable native Blob diagnostics. Every client read the shared evidence before and after an API/worker restart. Legacy Logs batch was separately exercised through REST. The report records the actual pinned package versions and operation-level assertions; no missing ecosystem was omitted.

The exact F1–F4 test union passed 74 tests after review fixes. The preceding affected regression union passed 174 tests before review. Eight new regressions reproduced and then resolved destination removal/re-add revocation, terminal diagnostic history restore, Resource Graph datetime/timespan serialization, expansion-budget truncation, nullable membership, bare Logs table aliases, integer bin precision, and empty-string coalesce semantics. The related query/delivery suite passed 55 tests. A broader unit/integration run remains separately tracked for final G; two subprocess failures were traced to an earlier checkout's editable installation and passed in this checkout's own environment. A stale migration-head expectation was corrected while preserving its storage-key assertions.

## Decisions and limits

- Diagnostic destination attachments have durable capability epochs. Removing and re-adding the same resource cannot revive old work. Category/table-mode changes affect future events. Terminal delivery history survives destination changes without becoming executable authority. Administrative restore alone reauthorizes resumable pending work against owned restored destinations.
- Logs `mv-expand` uses the sourced operator default; the separate local expansion budget fails explicitly rather than returning a silently truncated aggregate. Legacy `mvexpand` retains its 128-row operator default. String/null and integer semantics remain typed.
- Query resource accounting is conservative cumulative serialized allocation accounting, not an RSS or global-concurrency guarantee. Actual resource and two-replica measurements remain G gates.
- Supported sources are successful local Storage operations, Key Vault secrets and Administrative Activity. Failed/denied operations and other Activity categories are not generated. Some namespace/read details and virtual-child role-assignment creation remain documented limits.
- SQL inputs are authorized before joins. CPU evaluation runs off the event loop with cooperative cancellation; each legacy batch item owns a transaction so a cancelled query cannot poison later entries.
- Only the declared KQL subset and seven typed tables are supported. Full Kusto, resource-context Logs queries, workspace() syntax, statistics/render output, arbitrary AzureDiagnostics columns and Resource Graph facets/management groups remain unsupported. Exact deterministic dcount differs from production approximation.

The final reviewer made seven Important findings; the empty-string finding was promoted to Important because it changes nested typed predicates. All eight entered one tested fix pass; no second review was dispatched. No minor findings remain deferred for this phase. Earlier E was not re-reviewed.

[Query contracts](../query-contracts.md) and [language limits](../query-language.md) define the supported profile. This is representative local workflow qualification, not exhaustive Microsoft parity or final release qualification. G's combined matrix, scale, coverage, package parity and original collector gates remain required. Original MES malformed audit JSON remains the previously accepted documented upstream limitation. No real Microsoft tenant or VTW files were used. Local success is not a GitHub Actions success; the most recently observed Actions runs were blocked before execution by account billing/spending limits.
