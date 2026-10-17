# Default runtime and broader collection coverage

User direction: update the root checkout, make qualified changes default, archive stale
milestone material, and start broader persistent/queryable evidence coverage with the
fewest tasks. Execute Native in one branch/PR; prior reasonable-decision authority applies.
The root checkout has been fast-forwarded to merged main c8a0118.

## Outcome and boundaries
A fresh ordinary Docker setup gets Linux-volume PostgreSQL, TCP readiness, graceful
shutdown and identity initialization. Existing bind-backed instances keep their database
and identity; no automatic database migration or deletion. Historical evidence moves to
an indexed archive, while active tools/contracts remain usable. VTW, unrelated containers,
host trust/DNS and real tenants remain excluded.

One architectural extension starts broader evidence coverage: tenant-isolated Graph v1.0
security alerts (legacy and v2), incidents, Secure Scores and score control profiles.
These are collector-observed resources, stored in PostgreSQL through validated admin
imports and served through real authorized list/item HTTP routes. No arbitrary catch-all
success, fake detector, or real Defender connection. Device inventory already exists;
its collector coverage and remaining relationship gaps belong in the inventory.

## Default storage
New Compose initialization records a volume storage selector in compose.env. The Compose
file requires it, so old configuration cannot silently attach an empty volume. Re-running
initialization with existing legacy database files retains an explicit bind selector.
Missing legacy data fails closed. Existing credentials and instance keys are preserved.
Native startup remains available. Add a clean local Docker smoke and CI checks.

## Historical cleanup
Move completed specs/plans, execution/review/decision ledgers and superseded measurement
files into docs/archive/m1-m6 with an index and rewritten relative links. Keep current
contracts, operations guides and current stabilization evidence accessible. Do not move
old Compose/tool/test files that still have live consumers merely because their names
contain milestone numbers. Do not copy/move ignored runtime trees or user files.

## Collection coverage
Maintain a pinned-collector coverage inventory: each relevant command/request is covered,
partially covered with a named gap, or unsupported with a specific next surface. Existing
identity, Graph, Exchange, UAL and ARM contracts remain separate and reusable.

New security resource bodies derive from the pinned Microsoft v1.0 CSDL and official REST
references. Legacy alerts use SecurityEvents permissions; v2 alerts use SecurityAlert;
incidents use SecurityIncident; scores use SecurityEvents. Delegated security reads also
require documented persisted directory roles. Every page rechecks current identity and
authorization. Wrong tenant/audience, missing scope and missing role deny access.

Admin imports validate the complete batch before writing, require explicit string IDs,
preserve original timestamp strings and nested typed evidence, and upsert new versions
atomically under a tenant revision lock. Existing snapshots retain their version boundary.
List/get return only stored matching resources. Filtering is typed and server-side;
supported query options are endpoint-specific. Persist opaque expiring continuation
handles bound to tenant, caller, permissions, resource and snapshot. Unknown options,
invalid bodies and mismatched continuation use fail explicitly.

## Completion
Two implementation tasks: (1) defaults/cleanup, (2) coverage inventory and first security
evidence extension. Test real PostgreSQL persistence, query/filter/select where supported,
paging under update, restart resume, denies and tenant isolation. Exercise original MES
security alerts/Secure Score with real Graph commands and validate output contents; keep
any upstream mismatch visible without weakening API permissions. One final consolidated
review, corrections and relevant regression checks, then commit/push one PR. Broader
surfaces discovered by inventory are named backlog entries, never claimed as implemented.
