# SDD ledger — plan: docs/archive/m1-m6/superpowers/plans/2026-10-17-mockazure-mail.md

Base351b182, feat/exchange-audit, D:/projects/mockazure/.worktrees/mail.
M3C PR7 qualified522 unit/integration tests580.74s,20runtime gates/3SDKprofiles/20collector cases181.5455s.
Fresh M4 unit baseline210passed10.43s; production source identical at branch creation.
Ruling: delegated overnight design/plan decisions replace routine skill approval pauses. Self-review adopts the spec/plan; no claim that the user separately reviewed these new documents. Cost: design choices are reviewed after execution; preserve reversible commits and rulings.
Ruling: established project-local Git worktree fallback preserves prior runtime source; desktop native worktree tool is bound to unrelated projectless C: context and cannot select this D: repo. Cost: no app-native worktree attachment.
Ruling: baseline uses the just-completed522-test predecessor qualification plus fresh210-unit environment validation. No production source changed at creation; run full new branch suite after implementation. Cost: new environment DB wiring is verified when its own test runtime is provisioned.
Pre-flight Tasks1->2/3/7: canonical CSDL types/operation permissions feed mailbox/rule/UAL contracts; alias resolution is schema-local, never global self.*.
Pre-flight Tasks2->3/4/5: resolve_mailbox and current scope checks share canonical tenant/user identity. Mailbox deletion/rename and current role changes must affect each transport consistently.
Pre-flight Tasks2/3/4->6: mutations capture immutable mail audit facts atomically; state changes cannot publish success without evidence intent.
Pre-flight Tasks6->7: UAL jobs select retained audit records using a captured watermark. Pending-query retention pins and fenced materialization must be implemented together.
Pre-flight Tasks7->8/9: unchanged clients require schema-valid server-owned status and stable record pages; collector empty status is ignored input, never server state.
Pre-flight Tasks4->9: pinned Hawk configuration uses real compiled Get-EXO* calls plus Get-EXOCasMailbox; implement their actual server routes, no replacement cmdlets.
Pre-flight plan correction: Graph router registration is src/mockazure/admin/app.py, not a nonexistent providers/graph/router.py.
Review focus recorded in plan; exactly one final reviewer after implementation.

Task1 RED: four focused tests failed for missing schema-local self resolution, missing mail schema roots, and missing contract module. Resource response fixtures corrected to include server id; canonical CSDL ExchangeAdmin spelling replaces prose lower-camel in structural tests.
Task1 GREEN:10 focused tests0.46s;220 full unit tests8.15s. Public34-operation source manifest pinned; no tenant network calls.
Task1 Ruling: full source-backed decisions are committed in docs/mail-contracts.md: server-owned isReadOnly/id; schema-local aliases; bounded dynamic audit JSON only; AuditLogsQuery family overrides contradictory GET permission table; serviceFilter and enum input aliases resolved at operation layer; Exchange explicit command grants rather than guessed full built-in RBAC; Standard/Premium audit and Exchange entitlements explicit; delegated Purview roles distinct from Entra. Costs are documented alongside each ruling.
Task1 Ruling: operation create validation omits required server id while checking create-required writable fields. Response validation stays complete. Cost: partial structural validation is supplemented by explicit operation rules.
Task 1: complete (commits 66409a9..2334dec, tests: .venv/Scripts/python.exe -m pytest tests/unit/test_mail_contracts.py -q --tb=short --basetemp=work/pytest-mail-contracts → 10 passed in 0.41s)

Task2 RED:3 acceptance tests failed404 on missing authenticated mail settings route; real own PostgreSQL database/migrations ran successfully.
Task2 Ruling: Docker auto subnet pool exhausted. After inspecting assigned ranges, allocate isolated10.231.92/24 and ingress10.231.93/24, loopback55441, compose.test.m4.yaml, fresh private test-db.password. No predecessor resources changed.
Task2 Ruling: add Graph userPrincipalName PATCH to canonical directory mutation because mailbox rename fidelity requires the missing API operation. Actual pinned v1.0 user-update ref; cloud-only owned verified domain, serialized uniqueness, same existing permissions/history. Costs: small directory scope expansion; supported local domain model remains one verified domain. Predecessor negative test now checks unverified domain rather than all UPN writes.
Task2 Ruling: account-disable acceptance fixture must carry User.EnableDisableAccount.All plus User.Read.All in addition to rename permissions. Initial403 was correct and not counted as a product failure.
Task2 Ruling: native mailbox mutation facts are persisted atomically now; Task6 owns UAL projection/enqueueing and pending-source recovery before qualification. No intermediate UAL compatibility claim.
Task2 GREEN:227 unit/mailbox tests15.97s; role revocation, cross-tenant, disabled users, management auth, forwarding and immutable rename facts covered. Predecessor stale unsupported-UPN expectation corrected, rerun follows.

Task2 affected predecessor rerun:24passed16.73s (mail contracts, migrations, Graph mutations, directory evidence). Ruff/diff clean. Original unit/mailbox combined gate227passed15.97s.
Task 2: complete (commits 2334dec..95ac06f, tests: .venv/Scripts/python.exe -m pytest tests/integration/test_mailbox_state.py tests/integration/test_mailbox_boundaries.py tests/integration/test_mailbox_rename.py -q --tb=short --basetemp=work/pytest-mail-state → 7 passed in 8.19s)

Task3 RED:3 acceptance tests failed501 on absent rule routes; GREEN3passed5.45s; expanded5passed8.29s;233 unit/mail/rule/migration regressions25.82s.
Task3 Ruling: plan omitted rule-table migration; add026_mail_rules and shift planned audit/query migrations to027/028. Cost: internal migration numbering only; no released migration rewritten.
Task3 Ruling: reuse shared opaque continuation binding with immutable mail rule versions, SQL ordering and current access rechecks. One-hour pages, conservative history retention until M6 cleanup,100-item server pages and1000-rule local mailbox cap. Cost: explicit bounded profile and retained historical rows until hardening.
Task3 Ruling: insertion/reorder uses normalized one-based priorities, clamping requested positions beyond the list. Hidden entries retain their positions but are excluded from Graph output. Cost: unsupported sparse explicit priority values become normalized positions.
Task 3: complete (commits 95ac06f..1fb5607, tests: .venv/Scripts/python.exe -m pytest tests/integration/test_graph_mail_rules.py tests/integration/test_graph_mail_rule_boundaries.py -q --tb=short --basetemp=work/pytest-mail-rules → 5 passed in 7.95s)

Task4 RED: actual module missing-command400 and compiled GETMailbox501. Signed-token fixture originally used Exchange GUID but issuer's canonical audience is https://outlook.office365.com; corrected fixture then credited missing behavior. Mail authorization now derives resource audience from canonical RESOURCES. Seven mailbox regressions passed7.90s.
Task4 RED/GREEN: Disable-InboxRule recorded Set-InboxRule; regression failed then corrected requested operation. Proxy string declarations lost null; nullable forwarding parameters use object declarations with strict server validation. Five focused tests passed4.21s.
Task4 Ruling: observed original compiled OData routes and public generated CLR model metadata supply Exchange wire contracts; Graph CSDL does not describe EXO AdminApi. Persist concise derived property types plus archive hash. Cost: explicitly version-bound3.11.0 profile, unknown options/properties fail.
Task4 Ruling: add mail/sweep.py for explicit Sweep configuration persisted separately from Graph rules; no mail delivery engine claimed. Cost: bounded operational configuration, no fabricated delivery behavior.
Task4 Ruling: public module/container omitted Microsoft.Extensions.DependencyInjection.Abstractions8.0.0. Actual original Get-EXOMailbox reported FileNotFoundException after HTTP200. Add official NuGet artifact ef92b31065a36d110b7332690a226e6fe96d35430c6f3e40ff529053ee5d80ff to manifest and load unchanged assembly in harness. Direct OData and original cmdlets then deserialize successfully; no module source/DLL altered. Cost: one pinned client prerequisite.
Task4 Ruling: default bootstrap CommandName=* enumerates supported currently authorized commands, not arbitrary generated code. Explicit names remain strict. RED400 then GREEN200 with GlobalReader receiving only eligible commands.
Task4 Ruling: own full runtime on34443/55442 and10.231.94/24,10.231.95/24; reserve34444-34446 for native/MI. Existing runtimes preserved. Only16 named predecessor public archives copied with manifest hash checks; new dependency17th downloaded from official NuGet.
Task4 GREEN:239 unit/mail/Exchange regressions29.94s; six real module checks including primary14items5120bytes, archive3items2048bytes, two folders, CAS and fixed-proxy write cycle. Original compiled routes return actual typed values from PostgreSQL.
Task 4: complete (commits 1fb5607..1ede1ff, tests: .venv/Scripts/python.exe -m pytest tests/integration/test_exchange_mail.py tests/integration/test_exchange_scope.py tests/integration/test_exchange_compiled.py tests/integration/test_identity_exchange.py tests/unit/test_exchange_proxy_source.py -q --tb=short --basetemp=work/pytest-exo-task4-final → 7 passed in 7.36s)

Task5 RED:4 required acceptance tests failed on missing trace management404 and unsupported trace command400. GREEN4passed18.43s including5002-row continuation, default1000/max5000,90-day/10-day bounds, exact detail facts, scoped access, rolling100/300 budget and429 recovery.
Task5 Ruling: add027_mail_trace and shift nativeUAL/query migrations to028/029. Plan omitted trace persistence migration; isolated model module keeps table responsibilities explicit.
Task5 Ruling: preserve forensic Received values and require tenant Received+RecipientAddress uniqueness at import; reject ambiguous duplicate pairs409 atomically instead of inventing timestamps or stateful unreplayable continuation. Equal timestamps/different recipients and repeated recipients/different timestamps are supported and tested over5002rows. Cost: callers need precise timestamps for otherwise ambiguous duplicate-recipient ties; explicit bounded input profile documented, no claim of undocumented Microsoft behavior.
Task5 Ruling: rolling rate budget is tenant+command-wide,100requests/300seconds, independent committed SQL transaction before each trace read; current authorization runs before budget and again before data query. Cost: two transactions per trace query, necessary for cross-replica denial persistence.
Task5 Ruling: use explicit imported mailbox association for recipient scope, preserving foreign sender/recipient strings as evidence without deriving authorization from attacker-controlled addresses. Cost: importers must supply mailbox_user_id for scoped availability.
Task5 regression:232passed33.83s; Ruff/diff clean. No native trace collector claim before Tasks8/9.
Task 5: complete (commits 1ede1ff..abc9128, tests: .venv/Scripts/python.exe -m pytest tests/integration/test_exchange_trace.py -q --tb=short --basetemp=work/pytest-trace-task5-final → 4 passed in 20.58s)

Task6 RED:3 required tests reproduced duplicate no-op mail facts, absent UAL projection and unsupported UAL import422. GREEN3passed5.31s. Four tests including separate worker process/recreated API passed6.74s.
Task6 Ruling: use028_mail_audit after027 trace migration; extend shared auditLogRecord evidence family, existing scanner/fenced projector/receipt/export/retention machinery. No parallel UAL fixture store. Task7 owns active-query retention pins.
Task6 Ruling: UAL userId identifies application/system/user actors and may be a UPN; do not coerce it into a directory User FK/UUID. Tenant organization and creation timestamp remain checked explicitly. Cost: subject-specific risk aggregation is not applied to UAL, correctly separating these evidence families.
Task6 Ruling: pin additional official Common/Exchange Admin schema revisionf22300eab36df54f1efb07be8b23e229d1ff0537/SHA94968478423c3af5624b34c062631377f90eec90871f6a5a50b17d9275381e19; reference count36. Update test expectation for ModifiedProperties old absence to empty string per bounded Common representation. No fabricated clientIP; historical intent did not capture it, emit null and document.
Task6 RED/GREEN:3 credential cases bypassed initial audit-field validation through secret/name-value pairs; reject normalized private fields/pairs,4privacy tests passed0.49s.
Task6 test setup ruling: child worker must use existing mockazure.asyncio_support.run for Windows selector event loop, matching actual CLI; initial InterfaceError was fixture setup, not credited as a product failure.

Task6 broad regression:302passed168.97s including existing worker fences, retention, import/export, directory/authentication projection and mail/trace behavior.
Task6 RED:three focused tests exposed unbounded indexed UAL dimensions and case-sensitive tenant UUID comparison. Ruling: cap service128/operation256 characters in this profile; compare UUID values while preserving payload spelling. Cost: explicitly bounded imported names, protecting PostgreSQL index tuple size.
Task6 final focused gate:21passed7.07s; Ruff clean. Indexed bounds and UUID case fix verified after broad302test pass.
Task 6: complete (commits abc9128..557e2c7, tests: .venv/Scripts/python.exe -m pytest tests/integration/test_mail_audit.py tests/unit/test_mail_audit_bounds.py tests/unit/test_mail_audit_privacy.py -q --tb=short --basetemp=work/pytest-audit-task6-final → 11 passed in 6.95s)

Task7 RED:3 required query tests failed501 on absent routes. GREEN3passed4.50s.
Task7 expanded acceptance:9passed31.59s;1003row pages, immutable publication snapshot,
pending retention pins across generation reset, new DB/API instance, stale-worker
fencing and post-insert rollback, failed/exhausted leases, explicit410, workload/
tenant isolation, role revocation and escaped keyword filtering.
Task7 test setup ruling: restart fixture used unconfigured http://test host; corrected
to existing https://localhost:8443. Host guard rejection was expected and not a product RED.
Task7 Ruling: migration029 follows028; use existing EvidenceCursor pin for pending
query instead of parallel cleanup logic. Result lifetime7days/page1hour,100page cap;
expired row reclamation belongs to M6. Scoped permissions resolve explicit Common
workload names; unknownworkloads require all-service permission. No directory role
substitutes for a persisted Purview role. Status canonical enum is lowercase per
actual pinned CSDL (record types have PascalCase); incoming record enum aliases normalize.
Task7 Ruling: defaulttimewindow24hours at captured evidenceclock; max100filtervalues/
1024chars. Literal case-insensitive SQL keyword excludes Common indexed fields.
Costs and source references recorded in mail-contracts. Actual PostgreSQL process
restart remains the Task8 runtime gate, not claimed by API/engine recreation.

Task7 original MES source inspection found empty serviceFilter default. Focused unit RED reproduced400; normalize empty string to omitted optional filter, preserving strict nonempty conflicts. No original collector change.
Task7 affected regression255passed57.94s; empty-filter focused4passed4.36s; Ruff clean.
Task 7: complete (commits 557e2c7..bbf213c, tests: .venv/Scripts/python.exe -m pytest tests/integration/test_audit_queries.py tests/integration/test_audit_query_durability.py tests/integration/test_audit_query_access.py tests/unit/test_audit_query_input.py -q --tb=short --basetemp=work/pytest-query-task7-final → 10 passed in 33.83s)
Task8 RED:3 report/qualification tests failed for missing validator/report. Validator2tests GREEN0.06s;14 affected harness/schema/proxy units passed0.46s.
Task8 diagnostic gates: actual Graph Python native/container each8checks x application/delegated passed; Graph PowerShell8checks x2passed; original Exchange3.11.0 sevenchecks x2passed, including5003trace continuation/detail and exact persisted archive stats.
Task8 Rulings: pin official Graph.Mail87ab76a8a4cd0e96eb0644f601bea9ffde194b8986454fa61551ac65d19876e1 and Graph.Securityaa70e9bbcdd45006ea5e40b9021f811da2189655833c11d4656bde21791ef011, both2.35.1;19archives verified. Security module has no UAL cmdlets; original Invoke-MgGraphRequest supported interface is used. Its custom-environment raw provider omits Authorization, so harness passes supported Headers with same local token, as predecessor directory profile; originals unchanged.
Task8 setup corrections: malformed fixture namespace UUID corrected before data writes; diagnostic native profile expects clean-wheel34444, so use explicit ingress34443 copy when native replicas are absent. Missing local Python image built from pinned lock and pull_policy:never set. PowerShell correctly received403 but Exception.Message lacked status; oracle now reads Response.StatusCode/ErrorDetails. These are harness setup fixes, not service failure waivers.
Task8 full runtime/parity qualification pending; diagnostic successes are not yet milestone acceptance.
Task8 full qualification GREEN:19runtime checks,3Graph profiles x8checks x2authmodes,EXO7checks x2modes,124.154seconds. Source0ca6f7460d36ab1badfe365d18f15762acb63d91/fingerprint83ccb95fa0fe6560e91fadb8269b6cbf35bfaed021d22236affe3dd307cc58d0;wheel d5ecca60894928e7f7782cdae881fffc5ba2892dc11a16f3340aaec6c6da8053;image sha256:b9ac2c77ae57d7612a9ef9d8777328195f8371772c0a9358871f4a23afda2a20. Report acceptance3passed0.09s.
Task8 Ruling: native replicas advertise distinct origins; compare immutable page rows and normalized nextLink origin instead of requiring URLs to match literally. Initial assertion failed before temporary-rule cleanup; removed only that named probe rule through its API before rerun. Follow-up hardening: place temporary probe mutation cleanup in finally. All required behavior gates pass; no collector claim yet.
Task 8: complete (commits bbf213c..f8dbac8, tests: .venv/Scripts/python.exe -m pytest tests/unit/test_mail_report.py tests/acceptance/test_mail_distribution.py -q --tb=short --basetemp=work/pytest-mail-task8-final → 3 passed in 0.08s)

Task9 RED:3 missing collector-oracle assertions and missing ArchiveDatabase failed;
4 focused tests then passed0.69s. Actual original MES output RED showed blank
ForwardTo despite populated canonical recipient. Fixed bounded proxy ToString;
next original MES CSV preserved target0@example.test.247 affected unit/Exchange
tests passed37.76s;5 artifact corruption tests passed0.09s.
Task9 Ruling: stable virtual ArchiveDatabase derives from persisted archive GUID,
matching pinned CLR string type and original Hawk archive branch. No physical
database-placement model; cost if wrong: consumers requiring placement need an
explicit state model.
Task9 Ruling: preserve structured Exchange recipients while their fixed ToString
returns SMTP Address, using documented ADRecipientOrAddress shape as reference.
Cost if wrong: exact display-name formatting may need a richer typed projection.
Task9 Ruling: collector fixture has one combined forwarding/deletion rule and two
ordinary rules, plus sweep/hidden coverage. Multiple suspicious rules trigger
original Hawk JSON concatenation; this is documented as unqualified, never
silently accepted. Cost: broader upstream serialization coverage remains limited.
Task9 harness setup findings: original Hawk Test-GraphConnection helper omitted;
MES Graph UserIds takes string; MES UAL calls output initialization before logging,
requiring its original module LogLevel initializer. Correct helper closure/type
initialization, preserve original sources. Original 28-case diagnostic still
running; expanded audit formats/native evidence qualification remains pending.
Task8 follow-up: temporary runtime rule is now deleted in finally on assertion
failure. No M4 completion claim yet.

Task9 final collector gates:44 cases passed (11 workflow/format profiles x2auth
modes xpositive/denied). Seven original functions, all historical UAL formats,
native Set-Mailbox evidence, hidden real-module Get-InboxRule, sweep, forwarding,
rules and configuration. Exact 5003 traces and1003 historical UAL records per
positive mode.42 cases from fullbatch plus2fresh native reruns; failed diagnostics
preserved, no output repaired. docs/archive/m1-m6/mail-verification.json records this intermediate
output qualification; Task10 final source-bound combined gate remains required.
Task9 Ruling: original MES LogLevel initializer must accompany exact declarations;
prior failure was missing state, not enum reload. Reuse batch process with fresh
module scopes/connections/output dirs. Cost: future module-global state may need
extra initialization; current actual44cases verified.
Task9 native-query RED: case ConvertFrom-Json auto-converted fractional timestamps,
then string parameter coercion removed fractions, excluding native event. Persisted
query bounds proved this. Harness DateKind String preserves exact inputs; both
original native exports now contain the exact event.
Task9 Ruling: compare only declared native createdDateTime/Common CreationTime
as exact UTC instants; original PowerShell removes trailing decimal zero.
Regression RED->GREEN verifies equal formatting passes and1microsecond change
fails. All other native payload fields compare exactly.
Task9 artifact hash/path regression RED->GREEN; changed bytes or escaped paths
fail.7 collector-oracle tests and2report tests passed0.12s.
Full unit/integration pre-review regression:583passed1warning583.26s. Later added
hash/time oracle tests passed separately; final suite will include all after review.
Task 9: complete (commits f8dbac8..f00a93c, tests: .venv/Scripts/python.exe -m pytest tests/unit/test_mail_collector_oracles.py -q --tb=short --basetemp=work/task9-final → 7 passed in 0.05s)

Task10 Ruling: full regression is expected to pass, not intentionally fail as
the generic plan template says. Existing capability-contract assertions were
updated first and failed2tests because discovery still reportedM3C. Update
the advertised implemented operations and bounded limits; cost: clients keying
on old milestone metadata must accept the current advertised phase.
Task10 final review: one fresh reviewer, range351b182..d6885d2, four Important/no Critical/no Minor; all Important grades retained. Full disposition in docs/archive/m1-m6/mail-review.md. One consolidated fix pass, no rereview.
Ruling: serialize Exchange resolve/merge under mailbox lock and refresh rule ORM identities; deterministic Exchange/Exchange and Graph/Exchange RED both lost MarkAsRead, now GREEN with version/fact history. Cost: per-mailbox serialized edits, as required by ordering semantics.
Ruling: qualified Identity prefix must resolve within tenant and agree with explicit Mailbox; inbox/sweep GET/REMOVE RED returned200/wrong target, now400 mismatch/404 foreign or absent; valid alias retained. Fixture errors (missing tenant settings) corrected before behavioral RED.
Ruling: exact timestamp values required for historical formats too. Missing/time-shift/one-microsecond RED reproduced. Explicit UTC/en-US collector CSV representation accepted; omitted precision remains failure. Focused16passed9.22s then12oracle/reportpassed0.16s. Windows CSV fixtures require UTF8.
Ruling: supersede Task9 acceptance claim. Restore all three suspicious rules; keep original Hawk malformed JSON as required failing gate. Original MES trace CSV loses microseconds as independently verified by stronger oracle. No new waiver, no rewritten upstream files/artifacts. Reports preserve failed cases and nonzero exit.
Ruling: under user's overnight delegation, continue independent M5/M6 from repaired API branch while M4 remains draft/unqualified. Do not falsely complete Task10 or pass M6 release acceptance. Cost: later milestones carry visible external compatibility blockers.
Pre-review combined run atd6885d2 finished355seconds with historical shaped44cases and19runtimechecks; source/report retained in work, superseded by final post-fix required case run.

Task10 post-review full suite594passed1existingwarning619.02s. Runtime report807ac20:19checks/3Graphprofiles/EXO allpassed,385.201s;40of44originalcasespass. Exactly4fail:hawk_rules malformedJSON andmes_trace Receivedprecision, bothauthmodes. Strict acceptance1failed0.10s as required. Source/manifest/lock/wheel/artifact hashes independently verified; own temporary admin token absent. Ruff/diffclean.
Task10 status: implemented/reviewed, acceptance unresolved; do NOT mark complete or delete ledger. Draft PR, no merge. M5 design/plan already committed in its own worktree, baseline243unitspassed. Continue Native tasks, preserve these upstream gates for M6.
