# M4 execution record

M4 implements mailbox configuration, Graph/Exchange rules, message traces and
durable Graph Unified Audit Log queries. Work is isolated in the mail worktree
on feat/exchange-audit, based on qualified M3C commit351b182. PR7 remains for
user merge. VTW and predecessor worktrees/runtimes are untouched.

## Current status

Tasks1–8 are implemented. Task9's prior acceptance is superseded by final review.
Task10's one review and consolidated product fixes are committed at807ac20;
post-review regression passed594 tests in619.02 seconds (one existing MSAL warning).
M4 is **not qualified** and its PR will remain draft while required collector
outputs fail. M5/M6 continue as independent work under the user's overnight
delegation, carrying those blockers into combined release acceptance.

Confirmed original output defects: Hawk investigation JSON concatenates complete
documents for multiple suspicious rules; MES message-trace CSV drops fractional
seconds. All three forwarding rules now remain in the required fixture.
No output repair, hidden skip or new waiver is allowed.

The final source-bound run at807ac20 took385.201 seconds: all19 runtime checks,
three Graph SDK profiles and the real Exchange module passed. Of44 original
collector cases,40 passed and4 failed (Hawk inbox JSON and MES trace Received,
each in application/delegated mode). The strict acceptance test fails as required.
Source fingerprint,manifest,lock,wheel and source/output artifact hashes were
independently checked after the run; temporary collector admin token was removed.
Ruff and diff checks pass. No mandatory failure is converted to success.

The required44 cases cover seven original functions, both authentication modes,
positive/denied output,5003 traces,1003 historical UAL records in JSON/CSV/JSONL/
SOF-ELK, native Set-Mailbox evidence and hidden/sweep rules. Reports retain failures
and the verifier exits nonzero when a mandatory output fails.

## Runtime boundaries

- Test PostgreSQL: compose.test.m4.yaml, project mockazure-mail-tests, port55441.
- Qualification: compose.m4.yaml, project mockazure-m4, HTTPS34443, PostgreSQL55442.
- Clean-wheel native replicas:34444/34445, started and stopped by the verifier.
- Managed identity:34446. Internal client network:10.231.94/24; ingress:10.231.95/24.
- All certificates and credentials belong to this runtime. No host trust,
  DNS or firewall changes; no real-tenant connections.
- Migration029 is current. API replicas and workers share PostgreSQL state.

## Implemented tasks

| Task | Result |
| --- | --- |
|1|Pinned Graph v1.0 mail/UAL schemas, operation permissions and source rulings|
|2|Canonical mailbox/folder/archive/CAS/forwarding state and atomic mutation facts|
|3|Graph inbox-rule CRUD, ordering, hidden/read-only boundaries and stable version paging|
|4|Bounded Exchange HTTP command proxies and original compiled module routes|
|5|Persisted trace/detail rows, date/filter/scope checks, continuation and shared throttling|
|6|Native and imported UAL evidence with immutable actor/target facts and fenced projection|
|7|Durable SQL query jobs/results, workload permissions, Purview roles, retention pins and opaque pages|
|8|Clean-wheel/native/container clients, restart/replica/worker/fault and network-isolation checks|
|9|Original Hawk/MES execution and strict output oracles implemented; required output gates unresolved|

Task8's historical client-only report is
[mail-client-verification.json](mail-client-verification.json):19 runtime checks,
three Graph profiles with eight checks per authentication mode, and seven
original Exchange module checks per mode. That report predates Task9 and is not
the final M4 acceptance report.

Detailed contracts and delegated decisions are in
[mail-contracts.md](../../mail-contracts.md). The task ledger is preserved in
[mail-decisions.md](mail-decisions.md) before final publication. M6 owns expired
query/rule-history storage cleanup and the100,000-user/1,000,000-evidence profile.
