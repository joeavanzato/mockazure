# Cloud workflows program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution is selected; implement task-by-task in this session. Steps use checkbox syntax. Do not spawn implementation agents.

**Goal:** Deliver the approved Exchange, Azure resource, Storage, Key Vault, Graph and query/diagnostic expansion with official-contract fidelity and four-ecosystem qualification.
**Architecture:** One authoritative PostgreSQL state with focused service modules and service-specific public protocols. Shared transactions, content, current authorization and durable publication connect complete workflows without a generic success stub.
**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Windows/Docker, official Python/PowerShell/.NET/JavaScript-TypeScript clients.
**Spec:** [Approved cloud workflows specification](../specs/2026-10-18-cloud-workflows-design.md).
**Status:** Approved; A1-A4 are implemented and locally qualified, with phase A review pending. B-G remain pending. GitHub Actions is currently blocked before job execution by account billing/spending limits.

## Global Constraints

- No real Microsoft tenant is contacted. VTW is untouched.
- SMB/NFS mounts are explicitly deferred.
- Graph v1.0 schema and operation alignment for every Graph operation in this program.
- Runtime validation and CI must work from cached references without contacting Microsoft.
- Preserve Apache licensing, Windows/Docker support, PostgreSQL, existing data, and previously accepted collector limitations.
- No host trust, DNS or firewall changes; local endpoints and registered local receivers only.
- Keep existing secret/untracked files private. Do not stage unrelated changes or use blanket git add.
- Preserve current behavior outside deliberate documented contract changes. Commit often, push reviewable changes, attach created PRs, and do not auto-merge.
- Current COVERAGE describes implemented/qualified behavior; a plan or router entry is not support evidence.

## Review Focus

- Schema-correct JSON can still violate an action's status/permissions/headers: E1 plus A1 contract checks.
- Management rights must not silently grant Storage/Key Vault data access: B1, C1 and D3.
- A reset or expired worker lease must invalidate stale work: B4, C4, E4 and G2.
- A join or continuation must never bypass current tenant/resource authorization: A1, F1/F2 and G2.
- Official clients differ in signing, serialization and retry behavior: C1 and G1; all four ecosystems are required.

## Baseline and execution order

Fetched main is `e794f3a`, the merge of PR #25. Its runtime/source/tests/integrations/tools/CI tree matches design baseline `7660ca9`. Design commit `3129bdf` exists on `docs/cloud-workflows-design`. The pre-existing unstaged change to `src/mockazure/providers/graph/query/parser.py` is unrelated and must not be staged or reverted.

- [ ] Before implementation, fetch main, verify clean isolated execution worktree and merge ancestry, and include approved documentation without losing later main changes. Use the worktree skill at execution time; do not overwrite the root checkout.
- [ ] Run tasks A1-A4, B1-B4, C1-C4, D1-D4, E1-E4, F1-F4, then final G2-G3. Introduce G1 runner support incrementally with the first executable A/B profiles and extend it alongside C-F. This is one session with phase boundaries, not separate user-managed chats.
- [ ] A task begins by reading its phase plan, this index and the spec. Preserve exact cross-task interfaces. If an official contract contradicts a proposed signature/behavior, update the plan and contract record before implementation; do not manufacture parity.
- [ ] Add a targeted failing test, prove the failure is the missing behavior, implement, run the targeted/regression/client checks, update coverage/lifecycle, then commit. Group adjacent commits into a reviewable phase PR only while CI and dependencies remain clear.
- [ ] A phase is not qualified until its runtime/client checks pass. Final G requires all agreed families, not a subset with the remaining families deferred.

## Phase plans and dependency map

| Plan | Working deliverable | Required predecessors |
| --- | --- | --- |
| [A: Exchange](2026-10-18-cloud-workflows-a.md) | Audit search, recipients/delegation, security configuration, supported command RBAC | Existing evidence/mail stores |
| [B: ARM](2026-10-18-cloud-workflows-b.md) | Provider registry, data-action authority, account/vault/workspace/Compute/Network resources and durable lifecycle | A1 contract loader; existing ARM |
| [C: Blob and Files](2026-10-18-cloud-workflows-c.md) | Authenticated bytes, blocks, ranges, snapshots/versions, local clients | B1/B2; existing content |
| [D: Table Queue secrets](2026-10-18-cloud-workflows-d.md) | Typed entities/transactions, visibility/receipts, secret versions/recovery | B and C1 |
| [E: Graph workflows](2026-10-18-cloud-workflows-e.md) | Mail, calendar/recurrence/delta, local subscriptions | A2 delegation; B1 shared result/events; existing Graph/mail |
| [F: Queries and diagnostics](2026-10-18-cloud-workflows-f.md) | Resource Graph, Log Analytics, typed logs and actual local publication | B canonical resources/workspaces; C/D event sources |
| [G: Qualification](2026-10-18-cloud-workflows-g.md) | Four ecosystems, combined workflows, scale/recovery, release parity and coverage audit | G1 grows alongside A-F; G2/G3 require all phases |

## Contract acquisition is part of implementation, before handlers

A1 introduces `tools/acquire_cloud_contracts.py` with CLI:
`python tools/acquire_cloud_contracts.py --service SERVICE [--verify-only]`.
SERVICE is exchange, arm-providers, storage, keyvault, graph-workflows or queries.
The acquisition mode reads reviewed pinned upstream revisions/URLs and produces hash-bound local sources; verify-only must never use the network. Extend existing Graph/ARM acquisition rather than duplicating their parsers.

Per-service catalogs live in `integrations/contracts/cloud/<service>.json`; runtime schema/reference closures belong in `src/mockazure/cloud/data/<service>/` and are bundled in wheel/sdist. Source data retains upstream licenses. Acquisition can use official public docs/package repositories, never tenant endpoints, tokens or captured production data.

Each operation record has:
`operationId, service, version, method, path, aliases, command, parameterSets, requestSchema, responseSchemas, statusCodes, headers, permissions, queryOptions, limits, sources, limitations, tests, status`.
Use only applicable fields, with a discriminator separating REST operations and Exchange commands.
Each source has `url, revision, retrievedAt, sha256, localPath, license`.
Statuses are planned/implemented/qualified with explicit evidence references.

Before implementing an operation:

- [ ] Resolve its actual Microsoft method/path or cloud command parameter set and immutable source revision. Pin exact API/client versions in the catalog; do not choose a fabricated cross-service version.
- [ ] Include transitive schema dependencies and verify hashes offline. Preserve CSDL inheritance, enum/open types, writable/read-only distinctions and action-specific inputs.
- [ ] Write request/response wire-shape and semantic tests: correct success/error statuses, bodies, content types, headers, permissions, query behavior and parameter restrictions.
- [ ] Map existing/accepted profile limitations and unverified behaviors; source disagreement remains visible. Generic mocks, examples alone or successful SDK parsing are insufficient proof.
- [ ] Add official client results and exact local endpoint adaptations before marking qualified.

Graph references are v1.0 [metadata/OpenAPI](https://github.com/microsoftgraph/msgraph-metadata) and [operation documentation](https://learn.microsoft.com/en-us/graph/api/overview?view=graph-rest-1.0). Other APIs use [Azure REST specifications](https://github.com/Azure/azure-rest-api-specs), [Storage REST](https://learn.microsoft.com/en-us/rest/api/storageservices/), and official Exchange/Monitor documentation identified in phase plans.

## Shared file boundaries and contracts

Existing files verified during planning:

| Concern | Existing seam |
| --- | --- |
| Application/router/error dispatch | `src/mockazure/admin/app.py` |
| Canonical actor | `src/mockazure/identity/types.py: PrincipalContext` |
| Current identity and permissions | `src/mockazure/authorization/evaluator.py` |
| ARM inventory/current scoped rights | `src/mockazure/providers/arm/models.py`, `authorization.py`, `inventory.py` |
| Binary chunks and references | `src/mockazure/content/repository.py`, `models.py` |
| Durable job claim/dispatch | `src/mockazure/jobs/repository.py`, `dispatch.py`, `schemas.py` |
| UAL source materialization | `src/mockazure/auditlog/queries.py`, `worker.py`; `evidence/models.py` |
| Exchange commands/compiled transport | `src/mockazure/providers/exchange/commands.py`, `bootstrap.py`, `routes.py` |
| Mail config/permission evidence | `src/mockazure/mail/configuration.py`, `permission_records.py` |
| Graph validation and queries | `src/mockazure/providers/graph/schema_reference.py`, `query/` |
| Tenant/export/maintenance integration | `jobs/exports.py`, `evidence/cleanup.py`, `admin/maintenance_routes.py`, `workloads/lifecycle.py` |

New shared units have one owner: A1 owns `cloud/contracts.py`; B1 owns `cloud/types.py`, `cloud/events.py`, `cloud/models.py` and data-action authorization; B4 owns `cloud/lifecycle.py`; C1 owns Storage auth types/signing; C2 owns stream staging; F1 owns query types/compiler; G1 owns the qualification report protocol. Downstream tasks extend those interfaces, not duplicate authorities.

`session` is AsyncSession; `actor` is PrincipalContext; `now` is timezone-aware datetime. The function signatures in phase plans are proposed implementation interfaces, not claims that those functions already exist. Public response shapes always come from the service's pinned contract; internal dictionaries are not a substitute for that validation.

### Mandatory lifecycle integration per task

A new domain migration is numbered after the actual Alembic head (planning baseline 052). Register models with the existing metadata/migration mechanism. Do not assign competing hard-coded head numbers in parallel. Test upgrade from the preceding head and final upgrade from 052.

Every persistent domain adds typed export/import/reset/prune integration and tests before its commit is qualified. Before B4 exists, A uses its established mail/evidence lifecycle hooks; do not wait for the new shared lifecycle layer. B4 integrates new domains with existing tenant reset/export paths, not a second disconnected admin-only reset. Foreign-key/index design includes tenant identity and parent references. Partial invalid imports roll back.

Ordinary exports omit credentials, account keys and secret values. Explicit encrypted secret backup/restore is separate and must not be inferred from a normal export request. A restored tenant starts with fresh active credentials and no revived stale lease, old bearer session or delivery authority.

All public mutations emit eligible native evidence in the same transaction or commit no state. Domain facts map to documented audit/log streams; no universal invented event schema is exposed publicly. Outbox delivery is restart-safe and generation-fenced. Imported evidence remains distinguishable from user actions.

## Test commands and task completion

Use PowerShell commands from the execution checkout. Do not print database credentials. Existing test bootstrap:

```powershell
uv sync --locked --group clients
uv run python tools/prepare_tests.py
docker compose -f compose.test.m6.yaml up -d --wait
$env:MOCKAZURE_TEST_DB_PORT = '55445'
```

Each phase task supplies exact pytest paths. Execute `uv run python -m pytest <paths> -q` first for a meaningful failure, then after implementation for PASS. Failures caused by missing Docker/dependencies are not the required red test. Test assertion blocks show the expected assertions within the named scenarios; their fixtures/data are created by that task using existing isolated database fixtures, never added as production shims.

At each task completion:

```powershell
uv run ruff check src tests tools integrations
git diff --check
```

Run the targeted existing regression paths listed by the task and the new phase client profile where available:
`uv run python -m tools.verify_cloud_workflows --phase A --ecosystems python powershell dotnet typescript --report work/cloud/A.json`
(use the current phase letter). Exchange-specific operations require the official module; the four-ecosystem requirement is a program matrix, not an invented Exchange SDK in every language. The harness must label not-applicable cells and still require each applicable cell; final all-phase qualification cannot pass with an ecosystem missing.

The task's five steps end in an explicit-path commit. Include its schema catalogs, migration, lifecycle and COVERAGE changes, and only those files. If a listed Modify path is created by an earlier task, that dependency must already be present. All new public paths/command parameters get negative tests for unsupported options and current permissions. Local emulator limits are separate from Microsoft service limits.

Final commands after all domain tests pass:

```powershell
uv run python -m pytest tests/unit tests/integration -q --basetemp=work/cloud/final-tests
uv run python -m tools.verify_cloud_workflows --phase all --ecosystems python powershell dotnet typescript --report work/cloud/clients.json
uv run python -m tools.benchmark_cloud_workflows --records 100000 --binary-bytes 268435456 --readers 8 --report work/cloud/scale.json
uv run python -m tools.check_cloud_coverage --check
$cloudCommit = git rev-parse HEAD
uv run python tools/build_release.py --commit $cloudCommit --output work/cloud/artifacts
uv run python -m pytest tests/acceptance/test_cloud_distribution.py tests/acceptance/test_cloud_scale_report.py tests/acceptance/test_release_candidate_artifacts.py -q
```

G defines the new commands/tests before they are run. Artifact qualification starts from a committed runtime candidate; later prose-only commits must be linked to that exact runtime fingerprint. Preserve the existing release acceptance suites and their known upstream collector exception.

## Specification-to-task audit

| Requirement | Owning tasks |
| --- | --- |
| Every Graph operation uses actual v1.0 schema plus semantics | A1 contract rules, E1-E4, G3 |
| Exchange requested/common commands and operational policy | A1-A4 |
| ARM provider paths, accounts, vaults, workspace, Compute/Network | B1-B4 |
| Blob, Files REST, Table, Queue | C1-C4, D1/D2/D4 |
| Key Vault versions/auth/deletion/recovery/purge | D3/D4 and B3/B4 |
| Mail/calendar/recurrence/actions/delta/subscriptions | E1-E4 |
| Broad KQL with separate service profiles | F1/F2/F4 |
| Diagnostics to local Storage/workspace with queryable evidence | B1, C/D producers, F3/F4 |
| Current permissions, correct failures and state consistency | Each task; combined G2 |
| All four client ecosystems | G1 extended in A-F, final G3 |
| Persistence, migration, reset/export/restore, concurrency/scale | Each domain; B4/C4/D4/F4/G2 |
| Package parity, coverage and real CI evidence | G3 |

Plan self-review: all agreed families map to tasks; cross-task types and signatures are defined by their owner; five review-focus risks per phase are assigned to tests. No program code or dependencies were installed during planning. Schema revisions and client versions are acquired and verified as explicit implementation steps before consumers, not guessed here.

## Handoff

Review this index and linked phase plans. Native execution is already selected; do not ask the user to choose it again. Begin implementation only after plan review, using executing-plans and an isolated execution worktree. Apply the accepted spec, including its explicit exclusions, rather than interpreting “broad support” as unbounded parity with every Microsoft service.
