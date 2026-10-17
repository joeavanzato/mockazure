
# Bounded storage maintenance

Run the local authenticated API through the CLI:

    mockazure maintenance cleanup --tenant-id <local-tenant-id> --max-rows 500 --config <instance-config>

The HTTP operation is POST /_mock/tenants/{tenant_id}/maintenance/cleanup with
{"max_rows":500}. The integer range is1..1000, default500. Repeat while remaining
is true. This is an explicit administrative operation; no background scheduler
or real cloud service is contacted.

Each call deletes at most max_rows total rows across expired UAL query pins/results,
expired continuation handles and closed mailbox rule versions. Expired pending or
running UAL jobs are fenced failed, at most max_rows jobs per call. This extra count
is reported separately. Query/job tombstones remain so expired query requests retain
their authenticated410 response. Live queries, current rules and immutable evidence
receipts/provenance remain.

Active matching mailbox continuation snapshots pin every visible historical rule
version. Page capture, mutation and cleanup share the mailbox lock. A cleanup phase
skips mailboxes held by another operation, then rechecks pins after locking.

Audit and mailbox phases use separate transactions to preserve the established lock
order. Each phase rolls back atomically on failure; an earlier successful phase may
already have committed. Retrying is safe. The overall deletion budget spans both.
mail.remaining=null means the audit phase used the budget before mail was inspected;
overall remaining is conservatively true in that case.

This operation does not remove query/job tombstones, current mailbox state, source
receipts, database backups, credentials or certificate material. Existing evidence
and directory cleanup retain their separate documented semantics.
