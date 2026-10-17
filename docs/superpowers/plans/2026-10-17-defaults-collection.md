# Defaults and collection coverage implementation plan

> **For agentic workers:** Use superpowers:executing-plans. Native execution was selected;
> one consolidated whole-branch review, no implementation agents.

**Goal:** Reliable default installation and a first persistent Graph security evidence expansion.
**Architecture:** Reuse identity/authorization, Graph CSDL validation and typed query compiler.
A versioned PostgreSQL resource store provides security evidence snapshots and durable cursors.
**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker, PowerShell.
**Spec:** ../specs/2026-10-17-defaults-collection-design.md

## Global constraints
- No VTW changes, real tenant traffic, host trust/DNS edits, or ignored data cleanup.
- One branch/PR, Native execution, original client provenance, explicit unsupported behavior.
- Graph v1.0 official schema/REST contracts; existing accepted upstream limitations remain explicit.

## Review focus
- Existing credentials with missing database must not silently initialize empty storage.
- A mixed invalid import batch must leave no partial writes.
- Changed role/tenant/permission binding must reject an issued cursor.
- A fresh API instance must resume durable paging without duplicate/missing IDs.
- Archived documentation must not leave broken active links or hide compatibility limitations.

### Task 1: Default runtime and repository cleanup
**Files:** compose.yaml; src/mockazure/bootstrap.py; tests/unit/test_compose_init.py;
README.md; docs/index.md; docs/archive/m1-m6; docs/capabilities.md; docs/compatibility.md;
tools/verify_default.py; .github/workflows/ci.yml.
**Interfaces:** initialize_compose(project_dir, api_port, db_port) returns existing InstanceFiles;
compose.env adds MOCKAZURE_POSTGRES_STORAGE. No secret format changes.

- [ ] Add failing tests for fresh selector, legacy bind preservation, missing legacy data refusal
      and initialization idempotence; run tests/unit/test_compose_init.py.
- [ ] Implement explicit storage selection and tested readiness/identity lifecycle defaults.
- [ ] Move only tracked historical documents, rewrite links, reconcile current README/capabilities.
- [ ] Add a local default-stack smoke using isolated Compose project/ports and preserve its state;
      verify init, readiness, tenant persistence and restart. Add bounded CI regression/smoke.
- [ ] Run unit checks, documentation link verification and real Docker smoke; commit.

### Task 2: Collector inventory and Graph security evidence
**Files:** tools/collector_coverage.py; integrations/contracts/collection-coverage.json;
src/mockazure/collections/{models,contracts,repository,routes}.py;
src/mockazure/admin/collection_routes.py; src/mockazure/admin/app.py;
src/mockazure/db/alembic/versions/038_collection_evidence.py;
src/mockazure/authorization/catalog.py; tools/extract_graph_schema.py;
src/mockazure/providers/graph/schemas/v1.0.json;
tests/unit/test_collection_contracts.py; tests/integration/test_collection_evidence.py;
integrations/powershell/Test-CollectionEvidence.ps1; docs/collection-evidence.md.
**Interfaces:** POST /_mock/tenants/{tenant}/collections/{family}/import with records[];
GET /v1.0/security/{alerts,alerts_v2,incidents,secureScores,secureScoreControlProfiles}
and /{id}. Imports return revision and count; collection lists return value/nextLink.

- [ ] Inventory pinned original collector requests and map current routes/gaps; retain hashes.
- [ ] Pin official references and extend extracted CSDL structural closure.
- [ ] Write failing contracts and PostgreSQL tests for typed bodies, authorization, atomic import,
      query filtering/order/paging, snapshot mutation and restart, isolation and cursor rejection.
- [ ] Implement revisioned imports, resource-specific queries, durable bound cursors and routes.
- [ ] Extend permissions/capabilities; exercise real Graph SDK/PowerShell and original MES
      outputs for supported new invocations, recording any remaining upstream limitation.
- [ ] Run relevant full regressions, clean default smoke and package checks; commit.
- [ ] One independent whole-branch review; one consolidated fix pass; final verification,
      report current scope, push and create/attach PR.

## Initial evidence
Root main updated to c8a0118. Reused release worktree on feat/defaults-collection-coverage.
Baseline: 411 unit tests passed. First baseline attempt had an absent scratch parent;
the failed log is preserved; no source defect or waiver.
User asked to bundle and go; proceed under prior Native/reasonable-decision authorization
without another routine plan-approval pause.
