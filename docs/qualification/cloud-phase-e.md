# Graph workflow qualification

Runtime commit: `dc40b7949bd4a65ea5dc9850c90a218cb0e2f86a`. Source fingerprint: `d2ea8537bd4d3c119c75ea0a592ffefe18617cc154ae1e752d939736d7623e3f`.
The [machine-readable report](cloud-phase-e.json) passed against a fresh local Docker lab built from that committed source. All four ecosystems are required by the report validator.

- Original Python msgraph-sdk 1.64.0, Microsoft Graph PowerShell 2.35.1, .NET Microsoft.Graph 5.91.0, and JavaScript @microsoft/microsoft-graph-client 3.0.7 performed mail creation/update/send, attachment reads, recurring meetings, responses, calendar delta, and subscription management.
- Each client read all four clients' objects and updated events after API/worker restart. Subscription reauthorization/deletion used original generated operations; PowerShell primary calendarView/delta uses its original Invoke-MgGraphRequest as an explicitly labeled REST fallback.
- A private HTTPS receiver received real callbacks, returned 503, and received an identical payload again after API/worker restart. Deletion stopped subsequent callbacks. Windows and Linux recurrence expansion matched across the daylight-saving boundary.
- The named E test union passed 130 cases. Harness network/report regression passed 16 cases; global Ruff and diff checks passed. Five Important final-review findings were repaired with failing-then-passing regressions, including current-consent revocation, forwarding content, recurrence exceptions, alias attendees, and bounded exports.

Qualification fixes included schema-generated bound-action aliases and action parameter spellings, original .NET request-model setters, ISO PowerShell date values, subject-filtered delivery lookups, and a receiver with its own Docker network lifecycle. Readiness requires stable fresh verified TLS connections. Microsoft clients are unchanged; local transport configuration enforces local origins and the generated CA.

[Mail](../mail-contracts.md), [calendar](../calendar-contracts.md), and [notification](../notification-contracts.md) profiles retain their operation-level limitations. This is representative workflow qualification, not exhaustive Graph parity, throughput qualification, or a release gate. Basic notifications use registered local HTTPS receivers; rich encrypted notifications and external callback delivery remain unsupported.

The broader run recorded 1,572 passed, 4 skipped, and 15 failed acceptance prerequisites: missing qualification artifacts and a missing pinned Hawk archive. Those failures remain open for G; the full command is not green. GitHub Actions was blocked before execution by account billing/spending limits, so local evidence is not a CI pass. F and final G gates remain pending.
