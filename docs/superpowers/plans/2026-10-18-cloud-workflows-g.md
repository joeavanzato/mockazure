# Client qualification scale packaging and coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution is already selected. Steps use checkboxes; implement in this session.

**Goal:** Prove the full expansion through official clients and reproducible release artifacts with accurate coverage claims.
**Architecture:** Introduce one shared qualification harness incrementally with the first A/B workflows, then compose all phase profiles. Final gates verify committed source, cross-service state, scale/restart behavior and package parity.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker; official Python, PowerShell, .NET and JavaScript/TypeScript clients.
**Spec:** [Approved cloud workflows specification](../specs/2026-10-18-cloud-workflows-design.md).
**Program:** [Execution index and shared acceptance contract](2026-10-18-cloud-workflows.md).
**Status:** G1-G3 harnesses implemented; final combined native, full-scale and distribution qualification in progress. No final release pass claimed.

## Global Constraints

- No real Microsoft tenant is contacted. VTW is untouched.
- SMB/NFS mounts are explicitly deferred.
- Graph v1.0 schema and operation alignment for every Graph operation in this program.
- Runtime validation and CI must work from cached references without contacting Microsoft.
- Preserve Apache licensing, Windows/Docker support, PostgreSQL, existing data, and previously accepted collector limitations.
- No product code is introduced by this plan. After approval, commit cohesive changes, push reviewable PRs, and do not auto-merge.
- Read the program index for mandatory contracts, migration/lifecycle rules and exact test/client commands; those requirements apply to every task.
- Paths are repository-relative. New modules are labeled Create; add empty package initializers as needed. Allocate migration numbers after the current head, retaining the descriptive suffix.
- Use existing `PrincipalContext`, `AsyncSession`, `Database`, `JobClaim`, `JobResult` and `ContentRef` types; they are not new substitutes. Transaction ownership stays with callers except durable worker entry points.

## Review Focus

- A passing HTTP test must not be labeled official-SDK qualification (G1).
- A skipped/missing ecosystem must fail the required matrix, not reduce its denominator (G1).
- The tested commit and packaged/PR commit must match their recorded fingerprints (G3).
- Scale data must remain isolated across tenants and API replicas (G2).
- Coverage may not infer support from router discovery or broad family labels (G3).

## File boundaries and contracts

G1 is cross-cutting: introduce the harness and first executable Exchange/management profile alongside A1/B2, then extend it during C-F. It is not a prerequisite to writing the domain code or a separate scaffolding PR. G2/G3 are final qualification tasks after all phases.

The harness stages immutable official SDK/module versions from public package sources, records package integrity and licenses, and runs qualification against local services only. Client acquisition is distinct from runtime tests. Use each ecosystem's documented endpoint/credential configuration; label transport adaptation and REST fallback explicitly.

## Task G1: Four-ecosystem workflow runners and service coverage matrix

**Files**

- Create: `tools/verify_cloud_workflows.py`, `integrations/cloud-workflows-manifest.json`, `integrations/cloud-workflows-cases.json`, `integrations/dotnet/CloudWorkflows/CloudWorkflows.csproj`, `integrations/dotnet/CloudWorkflows/Program.cs`, `integrations/typescript/cloud-workflows/package.json`, `integrations/typescript/cloud-workflows/runner.ts`, `integrations/python/cloud_workflows.py`, `integrations/powershell/Test-CloudWorkflows.ps1`, `tests/unit/test_cloud_qualification_report.py`.
- Modify: `tools/prepare_clients.py`, `pyproject.toml`, `.github/workflows/ci.yml`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Create CLI `python -m tools.verify_cloud_workflows --phase A|B|C|D|E|F|all --ecosystems python powershell dotnet typescript --report PATH`. Each runner accepts `--profile PATH --workflow NAME --report PATH` (PowerShell uses equivalent named parameters); profile contains local endpoints/credential references, not committed secrets. Output JSON includes sourceCommit, sourceFingerprint, ecosystem, clientVersions, cases with operation IDs, transport (sdk/module/rest), expected/actual statuses, assertion results and elapsed time.

- [ ] **Step 1 — Add the named failing scenarios.** test_qualification_matrix_completeness feeds a report missing .NET and expects failure, feeds REST-only evidence marked SDK and expects failure, and verifies workflow IDs map to the operation catalog. Runtime profiles require the applicable services' official clients, with Exchange qualified through PowerShell module rather than fictional cross-language SDKs.

```python
assert validate_missing_ecosystem_report is False
assert validate_unlabeled_rest_fallback_report is False
assert required_ecosystems == {'python', 'powershell', 'dotnet', 'typescript'}
assert report_fingerprint == tested_source_fingerprint
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/unit/test_cloud_qualification_report.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Pin client packages and locks for Graph, Azure management/storage/secrets/query libraries in Python/.NET/TypeScript and Microsoft.Graph/Az/ExchangeOnlineManagement PowerShell modules. Supply per-workflow local fixtures, permissions and expected output. Test two-way cross-language interoperability: one client writes, another reads/updates. Isolate deterministic fault budgets from SDK retry cases. Each ecosystem must pass Graph and Azure representative workflows; service-specific REST fallback is explicit, justified and cannot replace required native-client coverage. Add CI profiles as their service phases land.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "test: qualify cloud workflows across four client ecosystems"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task G2: Cross-service evidence, scale and recovery

**Files**

- Create: `tools/benchmark_cloud_workflows.py`, `tests/integration/test_cloud_workflow_composition.py`, `tests/acceptance/test_cloud_scale_report.py`.
- Modify: `tools/verify_cloud_workflows.py`, `tools/release_report.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Create CLI `python -m tools.benchmark_cloud_workflows --records 100000 --binary-bytes 268435456 --readers 8 --report PATH`. Reuse exact public workflows from G1 and original network guard. Include persisted source IDs, hashes, result counts, worker/API restart markers, database/peak-process observations and request/failure metrics in report.

- [ ] **Step 1 — Add the named failing scenarios.** test_workflow_evidence_composition creates account/access/diagnostics, writes data, reads logs and audit evidence, revokes rights and checks denial. Scale harness distributes 100,000 records among inventory/diagnostic/Table datasets with each dataset's expected count declared; uses 256 MiB binary content, eight readers, concurrent writers, two API replicas and two restart points. A second tenant has distinct sentinel IDs and bytes.

```python
assert total_generated_records == 100000
assert object_size == 268435456
assert collected_ids == expected_authorized_ids
assert downloaded_sha256 == uploaded_sha256
assert cross_tenant_sentinel_hits == 0
assert lost_acknowledged_writes == 0
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_cloud_workflow_composition.py tests/acceptance/test_cloud_scale_report.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Exercise every spec acceptance workflow, queue contention, file-range writes, diagnostic fan-out, query joins, webhook receiver downtime and stale workers. Record exact result expectations and avoid guessed throughput promises; fail on loss/leakage/corruption/unbounded materialization. Include reset/export/restore/migration from 052 and current pre-change head, no secret export, role changes and service-specific 429 retry behavior. Qualification must not depend on real Microsoft services.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "test: add cloud workflow scale and recovery gates"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task G3: Release parity and verified coverage inventory

**Files**

- Create: `tools/check_cloud_coverage.py`, `tests/unit/test_cloud_coverage_index.py`, `tests/acceptance/test_cloud_distribution.py`, `docs/cloud-workflows-results.md`.
- Modify: `COVERAGE.md`, `tools/build_release.py`, `tools/verify_release.py`, `tools/verify_default.py`, `.github/workflows/ci.yml`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Create CLI `python -m tools.check_cloud_coverage --check` that maps operation manifests to coverage rows and qualification evidence. Do not autogenerate a support status from route presence. Extend release report with matrix/scale/schema-source artifact hashes and explicit missing/skipped failures.

- [ ] **Step 1 — Add the named failing scenarios.** test_coverage_index_requires_behavior_evidence feeds a planned/implemented-but-unqualified operation and verifies it cannot appear as qualified; an absent route cannot be supported. test_cloud_package_parity installs wheel/sdist and Docker artifacts built from the same source commit, runs representative A-F workflows and validates bundled schema/reference closure offline.

```python
assert inferred_support_from_route_only is False
assert missing_required_qualification_fails is True
assert wheel_fingerprint == sdist_fingerprint == docker_fingerprint
assert packaged_reference_hashes == source_reference_hashes
```

- [ ] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/unit/test_cloud_coverage_index.py tests/acceptance/test_cloud_distribution.py tests/acceptance/test_release_candidate_artifacts.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [ ] **Step 3 — Implement the declared interfaces.** Update per-operation COVERAGE fields and new service sections, legacy Logs batch label, local endpoint adaptations and all deliberate profile limits. Run all regression/client/scale gates and package parity before final claim. Record tested commit, versions, counts, known limitations and linked evidence. Inspect each PR base and main ancestry, attach created PRs and wait for required CI results; do not auto-merge or report queued CI as passing. Preserve original-MES documented limitation explicitly.
- [ ] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [ ] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "docs: publish qualified cloud coverage and release evidence"`. Preserve unrelated work. Push/review according to the program's phase PR rules.
