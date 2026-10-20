# Living Tenant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this program task-by-task. Native execution was selected previously and remains selected. Steps use checkbox syntax for tracking.

**Goal:** Deliver connected synthetic business content, basic operation-generated evidence, identity lifecycle and Graph hunting across 20 documented tables.

**Architecture:** Extend the existing population adapters/runner, PostgreSQL service state, evidence projectors and bounded KQL evaluator. Keep imported history separate from native service outcomes, and service-specific authorization separate from the shared query engine.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, SQLAlchemy/psycopg, PostgreSQL, existing job workers, packaged JavaScript console, Docker and native Windows deployment.

**Spec:** [Approved design](../specs/2026-10-20-living-tenant-design.md), approved 2026-10-20.

**Status:** A1–A6, B1–B3, C1–C6 and D1–D4 are implemented and locally qualified
as of 2026-10-20. Runtime candidate: `bab9eb9571ae901c4206b828c6807c929f450842`.
See the [final results, decisions and remaining limitations](../../qualification/living-tenant-results.md).
Hosted CI is blocked before execution by GitHub's billing/spending restriction.

## Global constraints

- “All runtime traffic stays inside the lab. VTW remains untouched.”
- “Collection failure simulation is deferred.”
- “Do not implement incident updates, comments, assignment, response actions or automatic incident-management workflows.”
- “Graph schemas alone do not describe every authorization or runtime behavior; the operation documentation and explicit local limitations are part of the contract.”
- “Importing an event cannot grant access or pretend an API call occurred.”
- “Advancing an evidence clock must not extend credentials or activate privileges.”
- “No new event broker or general event-sourcing rewrite.”
- “A run finishes when its declared work finishes; no always-on scheduler is introduced.”
- Preserve existing native/Compose deployment, Apache-2.0, shared-console authentication, API endpoint overrides, local CA validation and safe content downloads.
- No real tenant credentials, callbacks, invitation delivery or external data fetches. Official documentation acquisition is a build/development operation only.
- Do not delete user datasets, old worktrees, Docker networks or volumes. Disposable qualification resources must have unique ownership labels and verified names.
- Commit cohesive changes and push feature branches; do not automatically merge PRs.
- Preserve existing generators and resumable runs. Version new generators independently; never silently reinterpret old frozen previews.
- Tenant separation and current permissions apply before joins, aggregates and identifier disclosure.
- Pin Graph v1.0 contracts and hunting-table columns independently. Preview table availability is explicit.

## Review focus

1. A resumed content run binds the wrong user or file after a namespace change: reject inconsistent mappings, never reuse another tenant's IDs (A1/A3).
2. A denied operation rolls back and its evidence disappears, or exposes a foreign resource: retain safely attributed failure evidence without leaking the target (B2).
3. A deleted/restored identity or an expired PIM grant regains stale credentials or access: retain independent revocation and time checks (C2/C5).
4. A hunting join reaches a hidden table/workspace through its right-hand side: authorize every input before loading/evaluation (D2).
5. Cancellation or retry repeats a non-idempotent activity after an uncertain HTTP result: preserve needs-attention and existing receipt reconciliation (A6).

Each named test in the phase plans pins these cases. Existing restart/retry tests remain correctness regressions, not a new fault-injection product.

## Execution and source reconciliation

Planning base: `2745fec75b73cb6ae522fcef5ab809da9a43fed8` in
`D:\projects\mockazure\.worktrees\living-tenant`, branch `feat/living-tenant`.
This contains merged lab-console work plus the Windows launcher commit. Do not
assume launcher PR #34 has merged: inspect origin before implementation and record
the actual source SHA and PR dependency.

At execution start, check applicable AGENTS instructions and clean worktree status,
fetch origin, reconcile approved changes without discarding files, and record the
base and schema-source hashes in an execution ledger. Preserve the running root
checkout. This is one program, not separate user-owned chats.

Current observed Alembic head is `076_console_sessions`. Allocate numbered migrations
sequentially after the reconciled head, using the descriptive suffixes in each task.
New packages include `__init__.py` and database models must be registered with the
existing migration metadata. Do not hardcode an obsolete migration number.

Use the locked environment. On Windows, commands below written as `python` mean
`.venv\Scripts\python.exe -X utf8` (or the synchronized `uv run python` equivalent).
Integration tests use `tests/integration/conftest.py`, a uniquely owned PostgreSQL
test container/port, `MOCKAZURE_TEST_DB_PORT`, and the private
`.runtime/test-db.password`; never print credentials.

Planning itself needs no dependency install or running-service restart. Establish
baseline unit tests and the touched integration suites at implementation start.
Distinguish product failures from unavailable local prerequisites and CI billing.

## Phase plans and delivery order

| Phase | Tasks | Plan | Independently useful result |
| --- | --- | --- | --- |
| A | A1–A6 | [Connected content](2026-10-20-living-tenant-content.md) | Rich coherent datasets and finite activity from console/CLI |
| B | B1–B3 | [Basic evidence](2026-10-20-living-tenant-evidence.md) | Collected native successes and supported failures with provenance |
| C | C1–C6 | [Identity lifecycle](2026-10-20-living-tenant-identity.md) | Real access transitions, restoration and bounded Entra PIM |
| D | D1–D4 | [Graph hunting](2026-10-20-living-tenant-hunting.md) | Queryable 20-table security dataset and combined client qualification |

Nineteen implementation tasks. Order is A → B → C → D. D1's offline contract
acquisition may be brought forward after A1, but D3 depends on the earlier evidence
and identity interfaces. Execute natively; do not spawn implementer agents.
Use a final independent whole-program review under the selected Native workflow.

Prefer four phase PRs, each with cohesive commits, plus no separate scaffolding PR.
If earlier PRs remain unmerged, stack later branches on their tested predecessors
and identify dependencies. The user may merge them when ready; continuation need
not wait for a merge. Never close or merge the launcher PR as part of this program.

## Shared interface map

All existing signatures remain compatible unless the task explicitly introduces an
additive input. New signatures below refer to types defined by their owning task.

| Owner | Interface | Consumers |
| --- | --- | --- |
| A1 | `contracts.load_operation(key) -> OperationContract` | New public routes, coverage checker |
| A1 | `bindings.resolve(session, tenant_id, namespace, logical_key, service) -> EntityBinding` | Generators, content recipes, telemetry |
| A2 | `corpus.render(spec: ContentSpec) -> RenderedContent` | Mail, drives, Storage, hunting fixture hashes |
| A3 | `business.generate(name, parameters, refs, *, offset, limit) -> list[Mutation]` | Existing population dispatcher |
| A6 | `ActivityBatch(action_count, pacing_ms)` in frozen run input | Existing runner and console |
| B1 | `operations.mapping(operation_key) -> OperationEvidenceContract` | Service producers, qualification, coverage |
| B2 | `capture_failure(database, failure: FailureFact) -> UUID` | Authenticated data-plane error boundary |
| C5 | `effective_roles(session, tenant_id, principal_id, wall_now) -> frozenset[str]` | Existing role-aware authorization consumers |
| D1 | `table_contract(name) -> HuntingTableContract` | Ingestion, generators, query type binding, console |
| D1 | `ingest_rows(session, tenant_id, table, rows, source_ids, provenance) -> int` | Fixtures and internal native projectors |
| D2 | `run_hunting(session, actor, request, *, wall_now, limits) -> HuntingResponse` | Graph route and original clients |
| D3 | `project_hunting(source: SourceEnvelope) -> list[HuntingProjection]` | Existing durable evidence delivery |

Use existing `Mutation`, `ResourceBinding`, `SourceEnvelope`, `QueryLimits`,
`PrincipalContext`, and `ServiceAdapter` rather than creating incompatible copies.
Detailed field definitions and module paths are in the owning phase task.

## Common task procedure

Every task follows its listed test → observed failure → implementation → passing
verification → commit steps. A missing database/service is not a RED behavior test.
Run changed-path Ruff and `git diff --check` before each commit. Stage explicit paths.
Update the relevant operation manifest and COVERAGE.md when public behavior changes.

Per-phase qualification uses the actual candidate commit and an isolated local
instance. Pin original client versions and verify their vendor hashes. Endpoint/CA
configuration and documented collector workarounds are allowed; replacing collector
commands to force a pass is not.

The known MES concatenated-JSON limitation remains visible under its previously
approved exception. It does not excuse new failures. Hosted checks blocked by
billing remain blocked; only executed successful checks count as passing.

## Completion gates

- All 19 tasks have recorded verification and no unexplained acceptance omissions.
- Every declared workflow resolves to an operation/fixture row, source references,
  permissions, evidence mapping and qualification case.
- All 20 hunting tables have typed nonempty fixtures and positive/negative query cases.
- Old population profiles, migrations, sessions and authorization regressions pass.
- Native and Docker runs use the same packaged schemas/templates and show compatible
  results. Wheel/sdist checks include new source manifests and deterministic assets.
- Run full unit/integration regression once against the final candidate; earlier
  phase runs stay targeted unless a failure or shared-interface change justifies more.
- Final independent review is resolved; final results bind to the tested source.
- Publish user guide updates, cookbook, coverage and reproducible measured results.

## Spec coverage check

| Approved requirement | Owning tasks |
| --- | --- |
| Organization graph, service-specific identities and deterministic scale | A1, A3, A5 |
| Valid document bytes, copies, hashes, versions and relationships | A2, A3 |
| Mail/calendar, SharePoint/Teams and missing operational writes | A3, A4 |
| Intune observed evidence, Azure inventory, four Storage planes and secrets | A5 |
| Stacking templates, finite activity, console/CLI and durable progress | A5, A6 |
| Basic native successes, denied-call evidence, redaction and collection | B1–B3 |
| Joiner/mover/leaver, guests, restoration and credential/session lifecycle | C1–C4, C6 |
| Entra PIM and authorization independent of worker cleanup | C5, C6 |
| Graph hunting, all 20 tables, provenance, joins and readonly security data | D1–D3 |
| Optional overlays, ground truth and schema/browser/query discovery | D3, D4 |
| Migrations, original clients, package parity, scale and coverage | Each phase gate, D4 |

Self-review checked the approved scope, phase dependencies, interface names, the five
review-focus cases and the excluded features. No product tests were run for this
documentation-only planning change; execution must establish the recorded baseline.

## Repository destinations

The review copies are portable within `outputs`. At persistence time save:

- Design: `docs/superpowers/specs/2026-10-20-living-tenant-design.md`
- Program: `docs/superpowers/plans/2026-10-20-living-tenant.md`
- Phases: `docs/superpowers/plans/2026-10-20-living-tenant-{content,evidence,identity,hunting}.md`

Rewrite only these relative document links when copying; do not alter the approved
design's feature scope. Commit the approved spec and proposed plan as documentation.
