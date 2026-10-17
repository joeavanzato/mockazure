# M6 scale measurements in progress

The declared profiles remain 100/1,000, 5,000/50,000 and
100,000/1,000,000 users/sign-ins. No partial run counts as success.

The baseline small seed61002 passed exact concurrent paging and API/worker
restarts: 1,000 publications in52.571s (19.022/s). The baseline medium seed62001
imported all5,000users/50,000records in2.286s/38.210s, then failed its300s
projection budget with460published. Both used normal API imports and workers.
These historical measurements preceded the optimization and are not current
candidate qualification.

Read-only PostgreSQL inspection found both workers spending1.36–1.48s in
ordinary job selection. A source-key expression index and ordered claim index
address repeated generation lookups. A bounded historical sign-in path preserves
each source's own revision, receipt, original observation, risk cutoff, activity
projection and atomic completion. It services ordinary jobs before each batch.

The first batch experiment still joined the full backlog before LIMIT:
EXPLAIN ANALYZE inspected53,000sources and took333.552ms for one selection;
live selection also exceeded4.8s under contention. Selection now bounds jobs
before loading at most100sources by their primary keys. Source-key separators
are SQL constants so generic prepared plans can use the expression index.
Retired-generation jobs are excluded before the bound.

Seed61003's first optimized run was interrupted deliberately during diagnosis;
its retained non-success report must never be presented as acceptance.
Fresh measurements will follow the final bounded-selection implementation.

Verification so far: initial affected suite106pass and one fixed-head migration
test failure; updated migration/scale/CLI gates38pass8.24s. Worker/recovery
regressions23pass18.13s; generation-starvation regression RED0instead2, then
all8batch recovery tests pass4.31s. Final full qualification remains pending.

The next live sample identified repair scans taking0.427s every worker cycle and
ordinary claims taking0.261s under backlog. Repair now runs at most once per5s
per worker (run_one retains immediate repair by default). Migration037 adds the
ordered active queue index; constant predicates keep it usable by generic prepared
plans. Recovery RED missing repair parameter thenGREEN25tests19.01s; index gate
RED missing index thenGREEN27worker/ARM/recovery tests22.60s. Seed61004 was also
interrupted during diagnosis and remains non-success. Further fresh runs follow.
