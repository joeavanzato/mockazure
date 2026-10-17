# M4 final review and disposition

One fresh read-only review covered 351b182..d6885d2. No critical findings,
four important findings, no minors. This is the single review required by the
Native execution plan; no second review is scheduled.

1. Concurrent Exchange inbox-rule updates merged stale state before taking the
   mailbox lock. Confirmed by deterministic Exchange/Exchange and Graph/Exchange
   interleavings. Lock now precedes resolution/merge; rule reads refresh ORM state.
   Tests require both committed changes, version snapshots and accurate audit before/after.
2. Qualified rule Identity ignored a conflicting Mailbox. Confirmed for inbox and
   sweep reads/removals. Resolve the prefix within the tenant, reject mismatches
   with400, unknown/foreign prefixes with404; equivalent aliases remain valid.
3. Historical trace/UAL timestamp validation was incomplete. Tests now reject
   missing timestamps, one-microsecond changes and hour shifts in JSON, CSV,
   JSONL and SOF-ELK. Original collector CSV is interpreted in the explicitly
   pinned UTC/en-US container; representation changes are accepted, lost precision
   is not. Native UAL still compares the complete canonical payload.
4. The previous single-suspicious-rule fixture concealed an original Hawk output
   failure. All three forwarding rules are restored in the required case.
   Failed artifacts and hashes are retained; qualification remains failed until
   resolved. No new waiver is granted. Stronger timestamp checks also reveal
   original MES trace CSV loses fractional seconds.

The original reviewed historical all-green reports are superseded for acceptance.
A final post-fix source-bound runtime report and full regression are pending.

Delegated decision: continue independent M5/M6 work from the repaired API branch,
while publishing M4 only as a draft with its unresolved collector gates explicit.
The user's overnight continuation authorizes progressing independent work; it does
not justify reporting a failed collector gate as passed. M6 release acceptance
must carry these unresolved gates.

The reviewer accepted virtual ArchiveDatabase identity, recipient SMTP ToString,
schema enum/serviceFilter rulings, query/cursor expiration and trace duplicate-pair
rejection as explicit bounds. SMTP delivery, physical database placement, full
Exchange RBAC/SKU coverage and undeclared parameters are out of scope. M6 retains
expired storage cleanup and scale qualification. No other judgment was deferred.
