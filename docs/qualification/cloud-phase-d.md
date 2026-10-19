# Structured services qualification

Runtime commit: `589d7417e68f2b28d8b982807bad6f8724cdb3cb`. Source fingerprint: `5e5c67f30d6e7e4208e70394ab8600fbcd6202630ce028b3d97cfa2097f10ece`.
The [machine-readable report](cloud-phase-d.json) passed using a fresh Docker lab built from that committed source.

- Original Python, PowerShell, .NET and TypeScript clients each wrote and read Table entities, Queue messages and versioned secrets.
- All four repeated reads after API/worker restart and recovery after cloud import. Independent Python reads verified twelve cross-language objects before and after recovery.
- Metadata-only secret restore returned `409 SecretValueUnavailable`; explicit separately keyed encrypted backups restored values and versions. Imported Storage keys rotated and old keys were rejected.
- The complete unit suite passed: 587 tests. Structured-service plus ARM lifecycle regression passed: 103 tests, including original Python/.NET/TypeScript SDK tests. Ruff and diff checks passed.
- Final review found three Important issues: account-SAS upserts missing add permission, Table listing requiring service instead of container scope, and orphan secrets after resource-only fixture removal. Six cases failed before the fixes; all seven regression cases, including the unchanged Queue control, passed after them.

The PowerShell matrix uses original Az.Storage 8.1.0 and Az.KeyVault 6.6.1 modules. Table entity CRUD uses explicit REST because the module has no entity cmdlets. Key Vault uses an isolated container-only DNS mapping and TLS-preserving loopback 443 adapter for the original module's fixed-port URI builder. Host DNS/trust is unchanged. Client versions and per-operation transport are recorded in the JSON report.

Profile limits remain in [Tables](../table-contracts.md), [Queues](../queue-contracts.md), and [Key Vault](../keyvault-contracts.md). No throughput claim precedes G2. This report does not qualify the pending Graph/query/diagnostic phases or final release matrix. GitHub Actions has been blocked before execution by account billing/spending limits; local passing evidence is not a CI pass.
