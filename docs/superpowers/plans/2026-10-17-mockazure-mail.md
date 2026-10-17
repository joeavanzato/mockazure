# MockAzure Exchange and Unified Audit Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Native inline execution is the user's choice; no implementation agents.

**Goal:** Deliver stateful Exchange mailbox/rule/trace operations and durable Graph UAL searches that real clients and original collectors can use.

**Architecture:** Extend canonical PostgreSQL identities, tenant-scoped mailbox state and immutable evidence. Use fixed Exchange command transport and actual compiled-module REST routes, plus fenced UAL jobs and SQL-backed results.

**Tech Stack:** Python>=3.12, FastAPI, SQLAlchemy2, Alembic, PostgreSQL18, pinned Graph SDK/MSAL/ExchangeOnlineManagement3.11.0, Docker and native Windows.

**Spec:** docs/superpowers/specs/2026-10-17-mockazure-mail-design.md

M3C qualified at351b182 and PR7 is open. Execute under delegated overnight decisions.

## Global Constraints

- Only D:/projects/mockazure; no VTW access, real tenants or host trust/DNS/firewall changes.
- Separate M4 branch/runtime; preserve every predecessor runtime and worktree.
- Actual Graph v1.0 schema/operation references, explicit bounded capabilities and current authorization.
- Original Microsoft clients and original pinned Hawk/MES functions remain unchanged.
- No new required-output waiver; M3B's existing audit-JSON exception does not cover M4.
- Native execution, one final reviewer, frequent commits, pushed PR for user merge. Continue M5/M6.
- No registry publication. No assertions of unmeasured scale.

## Review Focus

1. Cross-mailbox/tenant identifiers and same-name aliases must never resolve foreign data; Task2/3/4 tests cover this.
2. Grant/role/identity/licence changes between bootstrap, polling and continuation must enforce declared current-state timing; Tasks4/7/8.
3. Equal timestamps/recipients and concurrent rule ordering must not lose or duplicate records; Tasks3/5/7.
4. Worker lease loss, source retention and API restart must not alter successful UAL results or publish partial success; Tasks6/7/8.
5. Original collector success messages cannot conceal empty/malformed/disclosed output; Task9 checks every artifact.

## File map

mail/ owns canonical mailbox/rule/trace state and audit facts; auditlog/ owns durable searches;
providers/ adapters own transport/serialization; administrative routes own explicit provisioning;
jobs/evidence retain typed fencing/provenance; integrations/tools own original-client qualification.

### Task 1: Pin mailbox/UAL schemas and operation permissions

**Files:** tools/extract_graph_schema.py; tools/extract_graph_permissions.py; src/mockazure/providers/graph/schemas/{v1.0,beta}.json; src/mockazure/mail/data/operations.json; src/mockazure/mail/contracts.py; docs/mail-contracts.md; tests/unit/test_mail_contracts.py

**Interfaces:** Consumes pinned Graph metadata and permission references. Produces validate_mail_payload(kind: str, body: dict, partial: bool=False) -> dict and operation_requirement(name: str) -> OperationRequirement; resolve contextual CSDL aliases per schema.

- [x] Write focused failing tests: test_security_alias_is_namespace_local validates messageRule and security.auditLogQuery/auditLogRecord including self.* enums without mapping other schema aliases incorrectly. test_mail_permission_matrix checks exact application/delegated kinds and known official IDs; AuditLog.Read.All and ThreatIntelligence.Read.All do not grant UAL. test_rule_contract_status validates create201/update200/read-only fields. Record exact EXO role/parameter/license references and all resolution rulings.
- [x] Run `.venv/Scripts/python.exe -m pytest tests/unit/test_mail_contracts.py -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [x] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [x] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [x] Run Ruff/diff checks, commit the coherent change and ledger the result.

### Task 2: Persist mailboxes, scopes, folders and configuration

**Files:** src/mockazure/mail/{models,schemas,repository,authorization,mailboxes}.py; src/mockazure/admin/mail_routes.py; src/mockazure/db/alembic/versions/025_mailboxes.py; src/mockazure/db/models.py; tests/integration/test_mailbox_state.py

**Interfaces:** Consumes Task1 contracts/current authorization. Produces resolve_mailbox(session, tenant: UUID, identity: str) -> Mailbox; require_mailbox_access(session, actor, mailbox, operation: str) -> None; mutate_mailbox(session, actor, mailbox, changes: dict, now) -> dict. Tenant/user FK and mailbox GUID/SMTP unique constraints; configuration and facts atomic.

- [x] Write focused failing tests: test_mailbox_identity_and_scope verifies ID/UPN/GUID/SMTP resolution, tenant separation, ambiguous aliases, current role revocation and user tombstones. test_forwarding_mutation validates internal recipient versus SMTP address precedence and actual saved values. test_mailbox_statistics_derive_from_persisted_folders verifies mailbox/archive/CAS data does not fabricate an active archive or statistics. Seed/import only through authenticated management endpoints.
- [x] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_mailbox_state.py -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [x] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [x] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [x] Run Ruff/diff checks, commit the coherent change and ledger the result.

### Task 3: Implement canonical Graph inbox rule lifecycle

**Files:** src/mockazure/mail/{rules,rule_projection}.py; src/mockazure/providers/graph/mail_routes.py; src/mockazure/admin/app.py; tests/integration/test_graph_mail_rules.py

**Interfaces:** Consumes Task2 mailbox resolver/access and Task1 schemas. Produces mutate_rule(session, actor, mailbox, operation: str, rule_id: str|None, body: dict, now) -> dict|None; rule_page(session, actor, mailbox, query, cursor) -> dict. Stable opaque rule IDs and SQL ordering.

- [x] Write focused failing tests: test_rule_crud_actual_sdk_shapes asserts create201, update200, delete204, schema-valid recipient/predicate structures and preserved IDs. test_mailbox_self_and_application_permissions tests delegated /me/self, app /users, foreign mailbox/tenant denial, read-only rules, hidden-rule exclusion and concurrent reorder. test_rule_page_snapshot verifies continuation under mutation and current authorization. Reject unsupported options and contradictory actions explicitly.
- [x] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_graph_mail_rules.py -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [x] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [x] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [x] Run Ruff/diff checks, commit the coherent change and ledger the result.

### Task 4: Expose Exchange command and compiled-module transport

**Files:** src/mockazure/providers/exchange/{probes,commands,bootstrap,routes,serialization}.py; src/mockazure/mail/{rules,mailboxes,authorization}.py; integrations/powershell/Test-ExchangeMail.ps1; tests/integration/test_exchange_mail.py; tests/unit/test_exchange_proxy_source.py

**Interfaces:** Consumes Tasks1-3 canonical state. Produces invoke_command(session, actor, name: str, parameters: dict, now) -> list[dict]; fixed_proxy_source(command_names: tuple[str,...], endpoint: str) -> str. Real compiled Get-EXO* routes discovered via unchanged pinned module calls and implemented as server transports.

- [ ] Write focused failing tests: test_exchange_configuration_and_rules asserts real state across Get/Set-Mailbox, inbox and sweep lifecycle, IncludeHidden, scoped owner/admin access and Global Reader denial for inbox rules. test_proxy_has_only_declared_operations runs real module requests and ensures data/command parameters cannot become executable code. test_compiled_exo_reads verifies mailbox/statistics/folders/CAS/archive through the original module, not replacement functions. Unsupported parameters error instead of silently succeeding.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_exchange_mail.py tests/unit/test_exchange_proxy_source.py -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [ ] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [ ] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [ ] Run Ruff/diff checks, commit the coherent change and ledger the result.

### Task 5: Implement trace and trace-detail query contracts

**Files:** src/mockazure/mail/{trace,trace_schemas}.py; src/mockazure/admin/mail_routes.py; src/mockazure/providers/exchange/commands.py; tests/integration/test_exchange_trace.py

**Interfaces:** Consumes mailbox scopes and operation contracts. Produces query_trace(session, actor, parameters: dict, now) -> list[dict] and query_trace_detail(session, actor, parameters: dict, now) -> list[dict]. Indexed immutable trace/recipient/detail rows, administrative import with provenance.

- [ ] Write focused failing tests: test_trace_window_defaults_and_limits tests90day retention/default48hours/max10day range/default1000/max5000. test_trace_received_recipient_continuation traverses5001+ records with equal timestamps and recipient ties without losses/repeats. test_trace_filters_and_scopes covers sender/recipient/status/MessageId/IP/traceID, exact detail events, unauthorized callers and foreign IDs. test_trace_throttle asserts429/Retry-After and recovery.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_exchange_trace.py -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [ ] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [ ] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [ ] Run Ruff/diff checks, commit the coherent change and ledger the result.

### Task 6: Project native mailbox and rule changes into UAL

**Files:** src/mockazure/mail/{audit,audit_schemas}.py; src/mockazure/evidence/{schemas,models,repository,projectors,datasets,cleanup}.py; src/mockazure/db/alembic/versions/027_mail_audit.py; tests/integration/test_mail_audit.py

**Interfaces:** Consumes immutable Task2-4 facts, existing SourceEnvelope/fenced projectors, Task1 security.auditLogRecord schema. Produces record_mail_audit(session, actor, operation, target, before, after, now) -> None and project_mail(source: SourceEnvelope) -> ProjectionBatch. Adds auditLogRecord family with tenant/source uniqueness and proper export/import contracts.

- [ ] Write focused failing tests: test_native_mail_audit_atomicity requires committed config/rule change and exact valid auditData/operation/user/object/service fields, no fact on rollback/no-op, no credentials or auth headers. test_mail_audit_replay_and_rename proves retry idempotency and immutable actor/target facts. test_historical_ual_import_export preserves provenance, tenancy, schema and timestamps. Worker/API restart preserves eventual publication.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_mail_audit.py -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [ ] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [ ] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [ ] Run Ruff/diff checks, commit the coherent change and ledger the result.

### Task 7: Build durable Graph UAL query jobs and record paging

**Files:** src/mockazure/auditlog/{models,schemas,queries,worker,paging}.py; src/mockazure/providers/graph/audit_query_routes.py; src/mockazure/jobs/dispatch.py; src/mockazure/evidence/cleanup.py; src/mockazure/db/alembic/versions/028_audit_queries.py; tests/integration/test_audit_queries.py

**Interfaces:** Consumes Task6 retained audit observations and existing JobClaim/lease/fence protocol. Produces create_query(session, actor, body: dict, now) -> dict; process_audit_query(database, claim: JobClaim, wall_now) -> JobResult; get_query/session list/records helpers with current authorization and SQL materialization.

- [ ] Write focused failing tests: test_query_create_poll_records exercises server-owned enum statuses, serviceFilter normalization/conflict rejection, exact filters and immutable results. test_query_workload_permissions ensures scoped permissions cannot read other services' metadata/results and old AuditLog.Read.All is insufficient. test_query_restart_lease_retention_races proves one winner, stale worker fencing, pending-source retention pins, retry/failure states, API/worker/PostgreSQL restart, stable1001+ record pages and410 expiry. Unknown options, pending/failed access and invalid time windows return explicit errors.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/integration/test_audit_queries.py -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [ ] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [ ] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [ ] Run Ruff/diff checks, commit the coherent change and ledger the result.

### Task 8: Qualify real SDK/module workflows and package/runtime parity

**Files:** integrations/python/graph_mail.py; integrations/powershell/{Test-GraphMail,Test-ExchangeMail}.ps1; integrations/{manifest.json,contracts/mail.md}; tools/{prepare_mail,verify_mail,mail_runtime}.py; compose.m4.yaml; compose.clients.m4.yaml; deploy/haproxy-m4.cfg; tests/unit/test_mail_report.py; tests/acceptance/test_mail_distribution.py

**Interfaces:** Consumes Tasks1-7 implemented surfaces. Produces source-bound work/mail-report.json with literal pass gates, exact versions, fingerprints, wheel/image hashes and per-profile checks. New own .runtime/m4, network/ports selected by actual probes; never reuse another runtime's credentials or modify host settings.

- [ ] Write focused failing tests: test_mail_report_fails_closed rejects missing/stale/false/fabricated checks and identity hashes. Run real MSAL+Graph SDK native clean-wheel and container Python, real Graph PowerShell/EXO application/delegated flows. Require two API replicas/workers, restart persistence, network guards, denied scopes/roles/licences, page reauthorization, fault retry and actual native evidence. No altered original service module or compiled Get-EXO replacement.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/unit/test_mail_report.py tests/acceptance/test_mail_distribution.py -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [ ] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [ ] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [ ] Run Ruff/diff checks, commit the coherent change and ledger the result.

### Task 9: Qualify original Hawk and MES output workflows

**Files:** integrations/powershell/Test-MailCollectors.ps1; tools/qualify_mail_collectors.py; tests/unit/test_mail_collector_oracles.py; docs/mail-verification.json

**Interfaces:** Consumes pinned original collector/helper sources and Task8 actual SDK/module sessions. Produces output identity/field oracles, source/output hashes, exact successful/denied cases for the spec's seven collector functions. Add any discovered required API endpoint; preserve original source and behavior.

- [ ] Write focused failing tests: test_mail_collector_oracles rejects omitted pages, wrong recipients/actions, malformed JSON, wrong UAL IDs, wrong trace continuation, denied artifact disclosure and non-literal pass values. Run original Hawk forwarding/inbox/configuration and MES mailbox rules/Graph rules/trace/UAL-v1 with nonempty expected records, both authentication modes supported by actual modules, and denied cases. Validate every produced CSV/JSON/text artifact against canonical state; no new failure waiver.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/unit/test_mail_collector_oracles.py -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [ ] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [ ] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [ ] Run Ruff/diff checks, commit the coherent change and ledger the result.

### Task 10: Run full regression, one review and publish M4

**Files:** README.md; docs/{capabilities,compatibility,mail-execution,mail-decisions,overnight-execution}.md; this plan

**Interfaces:** Consumes verified Tasks1-9 and predecessor contracts. Produces measured M4 support matrix, complete decision ledger and pushed PR based on qualified M3C branch. M5 begins from verified M4 without awaiting user merge.

- [ ] Write focused failing tests: Run all unit/integration/acceptance tests appropriate to the changed contracts, Ruff, package provenance and diff checks. Dispatch exactly one fresh final reviewer, regrade findings, reproduce/fix important findings in one pass and rerun full/runtime gates. Record all rulings/declined judgments/minors. Update real capability discovery and measured reports, commit/push/attach PR, preserve all runtimes and continue M5.
- [ ] Run `.venv/Scripts/python.exe -m pytest tests/unit tests/integration -q --tb=short` using an M4-owned basetemp/log/database. Expected RED for the missing behavior; fixture/setup errors must be corrected before crediting RED.
- [ ] Implement the named interfaces and source-backed operation contracts; record ambiguities as explicit rulings, preserving the spec's behavior.
- [ ] Run the same command and affected predecessor regressions. Expected PASS with exact behavioral assertions and no hidden skips.
- [ ] Run Ruff/diff checks, commit the coherent change and ledger the result.
