# MockAzure Milestone 1: Independent Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Deliver an independently runnable, PostgreSQL-backed MockAzure service with persistent tenant administration, authenticated management, durable export jobs, TLS and equivalent native/Docker entry points.

**Architecture:** A modular Python ASGI application and separate worker share PostgreSQL. Management endpoints exercise the real persistence and job infrastructure; Microsoft provider routes remain explicitly unsupported until later milestones. The authorized legacy source is preserved as a non-executable provenance reference and does not define the new package architecture.

**Tech Stack:** CPython 3.12, FastAPI, Uvicorn, Pydantic 2/pydantic-settings, SQLAlchemy 2 with psycopg 3 async support, Alembic, cryptography, Typer, HTTPX, PostgreSQL 18, uv, pytest/pytest-asyncio and Ruff. Resolve compatible stable dependency versions at execution, commit uv.lock, and record exact Python/PostgreSQL image digests. Use the installed uv to provision Windows CPython under the project if the MSYS Python installation cannot use the required wheels.

**Spec:** D:\projects\mockazure\docs\superpowers\specs\2026-10-16-mockazure-design.md

**Status:** Ready for review. No implementation tasks have been executed.

## Global Constraints

The following requirements apply to every task:

- Never edit, generate files, install dependencies, run tests or initialize tools inside E:\projects\vtw.
- All new project files, research downloads, generated credentials, data, exports and test output belong under D:\projects\mockazure.
- Do not recursively copy the VTW directory, any untracked files, lab/.mockcloud/, deploy/secrets/, lab/*.env or other environment/secret files.
- Runtime and integration tests never contact a real Microsoft tenant. There is no upstream fallback or transparent forwarding to Microsoft.
- PostgreSQL is the system of record; there is no SQLite alternative.
- Docker Compose is the primary quick start on Windows with Docker Desktop. The native Python/CLI installation has the same functionality and requires PostgreSQL.
- Licence: Apache-2.0.
- Commit coherent changes frequently. Attach the supplied remote and push once access works, after checking remote history. Never force-push over unknown history.
- Do not preserve permissive authentication, provider inaccuracies or consumer-specific special cases solely for source compatibility.
- No VTW integration, migration, deployment recipe or consumer-specific regression suite belongs in this milestone.
- Default configuration uses local endpoints and project-owned secrets. Do not change Windows trust stores, DNS, hosts files or global Git configuration.
- Keep all commands anchored to D:\projects\mockazure. Do not replace or repurpose HOME, home or CODEX_HOME.

The repository already exists on main with a documentation commit and origin set to https://github.com/joeavanzato/mockazure. Preserve its history. Execution must inspect repository instructions and status, then use the worktree skill's isolation procedure if required; do not create a separate user chat. Any isolated checkout must also remain under D:\projects\mockazure; the user's directory constraint takes precedence over automatic worktree locations.

## Review Focus

1. Concurrent initialization/migration must not replace credentials or corrupt schema; Task 2 tests concurrent file initialization and Task 3 tests serialized migrations.
2. A tenant-scoped job lookup must never expose another tenant's export; Tasks 3–5 test scoped reads, unique tenant identities and imports.
3. Container recreation must retain the actual PostgreSQL data directory and the same CA/admin credential; Task 6 recreates containers and verifies identity/state.
4. Worker crash/retry must not lose or duplicate an export or let an expired worker overwrite the new owner's result; Task 5 tests lease fencing and idempotence.
5. Host headers, missing files, database outages and invalid inputs must fail clearly without credentials or tracebacks leaking; Tasks 2 and 4 test these inputs.

## Milestone boundary and acceptance

M1 supplies administrative tenant metadata, not the Microsoft directory or cloud authentication model. App-only/delegated OAuth, Graph resources, policies, Exchange and ARM remain M2–M5. Capabilities must truthfully report no implemented provider operations in M1.

Acceptance:

- Start PostgreSQL, migrate, initialize local credentials/TLS, start API and worker.
- Create, list and retrieve tenants through authenticated management HTTP and the CLI.
- Export a consistent tenant snapshot as a durable job; import the supported format into a new tenant.
- Preserve tenant identifiers, exported data, CA identity and management credential across API, worker and database container recreation.
- Missing/invalid management credentials receive 401; unsupported Microsoft paths receive explicit errors.
- Exercise two API processes and two workers against one database without process-local coordination.
- Build and install a wheel independent of the repository checkout.
- Run all required tests with PostgreSQL. A skipped database/Docker suite is not a successful milestone.

Reset of future tenant resource collections, historical evidence seeding, full backup automation, provider pagination/delta and large-data qualification are explicitly later tasks. M1 documents operational backup requirements but does not claim a complete instance backup tool.

## File and interface map

Paths below are relative to D:\projects\mockazure.

| Files | Responsibility |
| --- | --- |
| pyproject.toml, uv.lock, .python-version, .gitignore, .gitattributes, LICENSE, NOTICE | Package, reproducible dependencies, repository hygiene and licence |
| reference/mockcloud/**, reference/provenance.json | Exact allowlisted source snapshot, excluded from wheel/runtime |
| tools/import_baseline.py | One-time tracked-blob importer; no execution/import of source code |
| src/mockazure/__init__.py, __main__.py, cli.py | Version and CLI composition |
| src/mockazure/config.py, bootstrap.py | Validated settings and idempotent local initialization |
| src/mockazure/tls.py | CA/leaf generation, reuse, SAN validation and explicit leaf renewal |
| src/mockazure/db/engine.py, models.py, migrations.py, alembic/** | Async database lifecycle and explicit migrations |
| src/mockazure/tenants/schemas.py, repository.py | Tenant types, validation and transactional metadata operations |
| src/mockazure/admin/app.py, auth.py, routes.py, client.py, errors.py | Management HTTP contracts, credential checks and CLI HTTP client |
| src/mockazure/jobs/schemas.py, repository.py, worker.py, exports.py | Persistent jobs, leases, snapshot validation and export handling |
| src/mockazure/commands/local.py, tenants.py, jobs.py | Local bootstrap/server commands and remote management commands |
| Dockerfile, compose.yaml, compose.test.yaml, .dockerignore | Distribution and isolated integration environment |
| tools/verify_foundation.py | Live end-to-end and restart verification |
| tests/unit/**, tests/integration/**, tests/acceptance/**, tests/conftest.py | Focused test layers |
| README.md, docs/foundation.md, docs/capabilities.md | Startup, state locations, administration and honest M1 coverage |

Unit tests may use temporary files and in-process ASGI clients. Database tests use real PostgreSQL, never SQLite or repository mocks as a substitute. Configure pytest basetemp as work/pytest so generated data stays in the project. Test databases have names beginning mockazure_test_ and are created on the dedicated local test PostgreSQL service.

Dependency caches and managed Python installations use project-local UV_CACHE_DIR and UV_PYTHON_INSTALL_DIR values. Runtime state goes in ignored .runtime/. Third-party research/test clients go in ignored work/vendor/. Exclude reference/, .runtime/, work/, tests/ and secrets from application distribution.

---

## Task 1: Traceable standalone package

**Files:** Create package/licence files, tools/import_baseline.py, reference/provenance.json and the four reference source files; create src/mockazure/__init__.py, __main__.py and cli.py.

**Interfaces:**

- Produces mockazure.__version__ = "0.1.0.dev0".
- Produces CLI mockazure --version and python -m mockazure with the same command entry point.
- Importer accepts --source PATH --ref REF --output PATH; its fixed allowlist is the four files in spec section 2.
- provenance.json format: schema_version=1, source_revision (full Git SHA), and files with source_path, destination_path, sha256 and git_blob_id. Do not include machine credentials or environment data.

- [ ] **Step 1: Inspect the destination status/instructions and preserve all existing work.** Confirm the only intended starting content is design documentation. Read source bytes only using git show at a resolved revision; do not execute any legacy module or recursively enumerate/copy its directory.
- [ ] **Step 2: Implement the importer and copy the four allowlisted tracked blobs.** Preserve exact bytes beneath reference/mockcloud/, retaining their source-relative layout. Refuse an existing conflicting destination. Record provenance. The source repository is read-only.
- [ ] **Step 3: Create the package and development configuration.** Use src layout and an explicit package include rule. Add Apache-2.0 text and a project NOTICE identifying the project and source origin without inventing third-party authorship. Use the account/project name for attribution pending any later preferred copyright wording.
- [ ] **Step 4: Establish reproducible tooling.** Declare runtime dependencies from the Tech Stack and a dev group containing test/lint/build requirements. Lock compatible releases with uv; select CPython 3.12 and record container digests when images are acquired. Ignore .runtime/, work/, .venv/, generated certificates, caches and build outputs. Exclude reference/ from Ruff, pytest discovery and both wheel/sdist packaging so the preserved snapshot is never reformatted, imported or executed.
- [ ] **Step 5: Verify package/provenance boundaries.** Run uv build, inspect wheel and sdist entries, and install the wheel into a clean project-local environment. Confirm mockazure --version and python -m mockazure --version print 0.1.0.dev0. Confirm the wheel contains no reference source, tests, runtime files or credentials. Recompute imported hashes against the manifest.
- [ ] **Step 6: Commit the independently installable package and source provenance.** Stage only explicit intended paths; commit message: build: establish standalone MockAzure package and source provenance.

No artificial unit tests are needed for copying or static packaging. The build/install checks establish the deliverable.

## Task 2: Local configuration, credentials and TLS

**Files:** Create config.py, bootstrap.py, tls.py, commands/local.py; extend CLI; create tests/unit/test_config.py, test_bootstrap.py and test_tls.py.

**Interfaces:**

- Settings(BaseSettings): data_dir: Path, database_url: SecretStr, listen_host: str, listen_port: int, admin_base_url: AnyHttpUrl, allowed_hosts: tuple[str, ...], pool_size: int, pool_max_overflow: int.
- load_settings(config_file: Path, environ: Mapping[str, str] | None = None) -> Settings.
- InstanceFiles frozen dataclass: config_file, admin_token_file, ca_cert_file, ca_key_file, server_cert_file, server_key_file, each Path.
- initialize_instance(data_dir: Path, *, database_url: str, advertised_host: str, port: int) -> InstanceFiles.
- CertificateFiles frozen dataclass: ca_cert, ca_key, server_cert, server_key, each Path.
- ensure_tls(data_dir: Path, hosts: tuple[str, ...]) -> CertificateFiles.
- renew_leaf(data_dir: Path, hosts: tuple[str, ...]) -> CertificateFiles.
- Admin CLI token is an opaque 256-bit random secret stored in admin.token. It is not an emulated Microsoft access token.

Configuration defaults: native listen 127.0.0.1:8443; admin base https://localhost:8443; pool_size=5 and pool_max_overflow=5 per process. Require an explicit PostgreSQL DSN; accept postgresql:// and postgresql+psycopg://, normalizing to the psycopg dialect. Do not fall back to a remote service or SQLite. Settings accept MOCKAZURE_ overrides and a selected local JSON config; environment overrides file values. Resolve relative paths against the selected configuration file's parent, never the process working directory. Derived token/certificate paths stay under data_dir. Default allowed hosts include localhost, 127.0.0.1 and configured service aliases.

- [ ] **Step 1: Write failing tests for required inputs, reuse and certificate identity.** Include these assertions:
~~~python
assert load_settings(config).listen_port == 8443
assert "secret-password" not in repr(load_settings(config))
with pytest.raises(ValueError):
    load_settings(config_with_sqlite_url)
assert initialize_instance(path, **options) == initialize_instance(path, **options)
assert token_before == token_after
assert ca_fingerprint_before == ca_fingerprint_after
assert "localhost" in leaf_dns_names
assert "127.0.0.1" in leaf_ip_addresses
assert leaf_issuer_matches_ca
~~~
Also test concurrent initializers produce one complete credential set; malformed/partial files fail without replacement; expired or mismatched leaf certificates request explicit renewal; paths with spaces work.

- [ ] **Step 2: Run uv run pytest tests/unit/test_config.py tests/unit/test_bootstrap.py tests/unit/test_tls.py -q and observe failures caused by missing behaviour.**
- [ ] **Step 3: Implement settings and file initialization.** Create files atomically using exclusive creation and a cross-process initialization lock. Avoid import-time side effects. Store only a local configured DSN; mask it in logs/errors. Do not put raw secrets in exceptions or normal command output.
- [ ] **Step 4: Implement certificates and local commands.** Generate an instance CA and server certificate using cryptography. Include localhost, 127.0.0.1 and configured service aliases; require ServerAuth, CA constraints and appropriate key usage. Retain the CA private key for leaf renewal. Use a 365-day leaf and 10-year CA validity, with five minutes of clock skew. Do not automatically trust or rotate the CA.
- [ ] **Step 5: Add mockazure init and mockazure cert renew commands.** init writes configuration and prints only paths/nonsecret endpoints; accept DSN through an environment/config input rather than requiring it on a visible command line. Missing inputs exit 2; configuration corruption exits nonzero with an actionable message.
- [ ] **Step 6: Run the focused tests and a real HTTPS certificate-verification check against the generated CA.** Confirm renewal preserves the CA fingerprint and replaces only the leaf/key as requested.
- [ ] **Step 7: Commit:** feat: add project-local initialization and TLS.

## Task 3: PostgreSQL schema and persistent tenants

**Files:** Create db/engine.py, db/models.py, db/migrations.py, db/alembic/**, tenants/schemas.py, tenants/repository.py; create tests/integration/test_migrations.py, test_tenants.py and database fixtures in tests/conftest.py; add the db service in compose.test.yaml.

**Interfaces:**

- Database dataclass: engine: AsyncEngine, sessions: async_sessionmaker[AsyncSession].
- create_database(settings: Settings) -> Database; async close_database(database: Database) -> None.
- migrate(database_url: str) -> None; async schema_is_current(database: Database) -> bool.
- TenantCreate(BaseModel): slug: str, display_name: str, domain: str, licences: list[str] default empty.
- TenantView(BaseModel): id: UUID, slug: str, display_name: str, domain: str, licences: list[str], revision: int, created_at: datetime.
- async create_tenant(session: AsyncSession, data: TenantCreate) -> TenantView.
- async get_tenant(session: AsyncSession, tenant_id: UUID) -> TenantView | None.
- async list_tenants(session: AsyncSession, *, after: UUID | None, limit: int) -> list[TenantView].
- TenantConflict exception for duplicate slug or domain.

Validation: slug is lowercase ASCII [a-z0-9-], 1–63 characters, no leading/trailing hyphen; domain is lowercased ASCII DNS form, no scheme/path, maximum 253 characters; display_name is trimmed, nonempty, maximum 200 characters. Licence strings are nonempty bounded labels, deduplicated. Reject unknown input properties. Tenant IDs are server-created UUIDs; revision starts at 1.

- [ ] **Step 1: Write failing PostgreSQL tests.** Test initial migration and repeat migration, two tenants with distinct IDs, duplicate slug/domain conflicts and data retrieval after disposing/recreating the engine:
~~~python
a = await create_tenant(session, tenant_a)
b = await create_tenant(session, tenant_b)
await session.commit()
assert a.id != b.id
assert a.revision == 1
assert (await get_tenant(new_session, a.id)).domain == tenant_a.domain
assert [x.id for x in await list_tenants(new_session, after=None, limit=1)] == [min(a.id, b.id)]
~~~
Test concurrent inserts of the same slug yield one success and one TenantConflict. Test Unicode display names, invalid domains, oversized inputs and independent tenant data.

- [ ] **Step 2: Start the isolated PostgreSQL test service and run uv run pytest tests/integration/test_migrations.py tests/integration/test_tenants.py -q.** Missing schema/functions must fail visibly. Refuse a test DSN whose database name lacks the mockazure_test_ prefix.
- [ ] **Step 3: Implement the engine and initial Alembic migration.** Store UUID IDs, unique normalized slug/domain, bounded display name, JSONB licence list, integer revision and timezone-aware created_at. Use parameterized queries, bounded pools and explicit transaction ownership in callers.
- [ ] **Step 4: Serialize migration execution with a PostgreSQL advisory lock held on the migration connection.** A second migration runner waits and rechecks. API processes check readiness but do not run migrations automatically. Define mockazure db upgrade and db status commands using this implementation.
- [ ] **Step 5: Implement repositories and test serialization.** List tenants in UUID order with after/limit (default 100, maximum 500 at the HTTP layer). Convert unique-constraint violations to TenantConflict without disclosing SQL/DSN content. Verify async psycopg execution on native Windows.
- [ ] **Step 6: Run the focused tests and a two-process migration check.** Both migration commands exit 0 with one schema version; recreated connections read identical rows.
- [ ] **Step 7: Commit:** feat: persist tenants with PostgreSQL migrations.

## Task 4: Authenticated management HTTP and CLI

**Files:** Create admin/app.py, auth.py, routes.py, client.py, errors.py, commands/tenants.py; extend local commands/CLI and tenants/schemas.py for TenantPage; create tests/integration/test_admin_api.py and tests/unit/test_admin_auth.py, test_admin_client.py.

**Interfaces:**

- create_app(settings: Settings, database: Database | None = None) -> FastAPI.
- verify_admin_token(presented: str, token_file: Path) -> bool; compare fixed-length digests in constant time.
- AdminClient(base_url: str, *, ca_file: Path, token_file: Path) async context manager; all HTTP methods are async.
- AdminClient.create_tenant(data: TenantCreate) -> TenantView; get_tenant(tenant_id: UUID) -> TenantView; list_tenants(after: UUID | None = None, limit: int = 100) -> TenantPage.
- TenantPage(BaseModel): items: list[TenantView], next_after: UUID | None.
- AdminError body: {"error":{"code":str,"message":str,"request_id":str}}.

M1 endpoint contract:

| Method/path | Success | Failure |
| --- | --- | --- |
| GET /_mock/health | 200 {"status":"ok"} without tenant/secrets | Process is reachable only; no DB claim |
| GET /_mock/ready | 200 {"status":"ready"} | 503 for unavailable DB/outdated schema; sanitized body |
| GET /_mock/capabilities | 200 milestone=1, provider_operations=[], named admin operations | Requires admin authentication |
| POST /_mock/tenants | 201 TenantView, local Location | 401, 409 TenantConflict, 422 ValidationError |
| GET /_mock/tenants | 200 TenantPage, default 100/max 500, after UUID | 401, 422 |
| GET /_mock/tenants/{id} | 200 TenantView | 401, 404 TenantNotFound |
| Other provider paths | No success in M1 | 501 MockAzureOperationNotImplemented for configured service paths |
| Unknown administrative paths | No success | 404; supported path/wrong method returns 405 |

Except health/readiness, management endpoints require Bearer admin.token. This is a local administrative interface, not an Entra authority. Unknown provider paths can return their explicit unsupported error without disclosing tenant data; no request is forwarded.

- [ ] **Step 1: Write failing HTTP/authentication tests.**
~~~python
assert (await client.get("/_mock/tenants")).status_code == 401
created = await client.post("/_mock/tenants", headers=admin_headers, json=tenant_payload)
assert created.status_code == 201
assert created.headers["location"].startswith("https://localhost:8443/")
assert (await client.get("/_mock/capabilities", headers=admin_headers)).json()["provider_operations"] == []
assert (await client.get("/v1.0/users")).status_code == 501
~~~
Test invalid token, malformed UUID, duplicate tenant, unknown properties, bounded list paging, hostile Host, database unavailable and out-of-date schema. No response/log may contain the raw token, DSN password, SQL statement or stack trace.

- [ ] **Step 2: Run uv run pytest tests/unit/test_admin_auth.py tests/unit/test_admin_client.py tests/integration/test_admin_api.py -q and observe missing behaviour failures.**
- [ ] **Step 3: Implement the ASGI lifespan, error handlers and HTTP routes.** Own one engine per process and dispose it on shutdown. Trust only configured Host values; construct Location from admin_base_url. Disable default docs/openapi endpoints unless explicitly made authenticated.
- [ ] **Step 4: Implement the management client and tenant CLI.** Commands: mockazure tenant create --slug --display-name --domain; tenant list; tenant show ID; capabilities; health. Client reads the token file, verifies TLS with ca_file and rejects redirects to a different origin. Commands emit JSON for machine-readable results; non-success responses exit nonzero.
- [ ] **Step 5: Implement mockazure serve --config PATH using Uvicorn and generated certificate paths.** Default native bind stays 127.0.0.1. Docker can explicitly bind 0.0.0.0 while publishing only loopback host ports.
- [ ] **Step 6: Run focused tests, then create/retrieve a tenant through the live CLI over HTTPS.** Verify wrong CA fails. Capture outputs under work/ and redact credentials.
- [ ] **Step 7: Commit:** feat: expose authenticated tenant administration.

## Task 5: Durable tenant exports and worker recovery

**Files:** Create jobs/schemas.py, repository.py, worker.py, exports.py and commands/jobs.py; extend db models/migrations, admin routes/client, tenant commands; create tests/integration/test_jobs.py, test_exports.py.

**Interfaces:**

- ExportSnapshot(BaseModel): schema_version: Literal[1], source_tenant_id: UUID, captured_at: datetime, tenant: TenantCreate. Credentials/sessions/keys are not fields; reject unknown properties.
- JobView(BaseModel): id: UUID, tenant_id: UUID, kind: Literal["tenant.export"], state: Literal["pending","running","succeeded","failed"], attempts: int, error_code: str | None.
- ClaimedJob frozen dataclass: id: UUID, owner: UUID, attempt: int, payload: ExportSnapshot.
- async enqueue_export(session: AsyncSession, tenant_id: UUID) -> JobView.
- async get_job(session: AsyncSession, tenant_id: UUID, job_id: UUID) -> JobView | None.
- async get_export_result(session: AsyncSession, tenant_id: UUID, job_id: UUID) -> ExportSnapshot | None; the HTTP caller checks get_job first to distinguish missing versus not-ready.
- async claim_job(session: AsyncSession, *, owner: UUID, now: datetime, lease_seconds: int) -> ClaimedJob | None.
- async finish_export(session: AsyncSession, claim: ClaimedJob, result: ExportSnapshot) -> bool.
- async fail_job(session: AsyncSession, claim: ClaimedJob, *, error_code: str, now: datetime) -> bool.
- async run_one(database: Database, *, owner: UUID, now: datetime) -> bool.
- async import_tenant(session: AsyncSession, snapshot: ExportSnapshot, *, replacement: TenantCreate | None = None) -> TenantView.
- Extend AdminClient with async enqueue_export(tenant_id: UUID) -> JobView, get_job(tenant_id: UUID, job_id: UUID) -> JobView, get_export_result(tenant_id: UUID, job_id: UUID) -> ExportSnapshot and import_tenant(snapshot: ExportSnapshot, replacement: TenantCreate | None = None) -> TenantView.

Database: jobs table with UUID, tenant foreign key, kind, state, attempts, available_at, lease_owner, lease_until, payload JSONB, result JSONB, sanitized error_code, timestamps. Index claimable jobs by state/available_at. Jobs capture the tenant snapshot transactionally at enqueue, so later tenant changes cannot change an existing export.

Lease defaults: 30 seconds; maximum 3 attempts; failure retry delay 1 second then 2 seconds. Claim with row locking/SKIP LOCKED in a short transaction. An expired running job is reclaimable until its attempt limit. A claim is fenced by job ID, owner and attempt; stale workers cannot complete or fail a newer claim.

Additional management routes:

- POST /_mock/tenants/{id}/exports -> 202 JobView and local Location.
- GET /_mock/tenants/{id}/jobs/{job_id} -> 200 JobView; wrong tenant/job pair returns 404.
- GET /_mock/tenants/{id}/jobs/{job_id}/result -> 200 ExportSnapshot on success; 409 JobNotReady otherwise.
- POST /_mock/tenants/import -> 201 TenantView from schema_version=1; optional replacement metadata allows a new unique slug/domain. Always create a new ID; never overwrite an existing tenant. Conflict=409, invalid version/data=422.

- [ ] **Step 1: Write failing tests for two workers, consistent snapshots and fencing.**
~~~python
assert first_claim.id != second_claim.id
assert await finish_export(session, stale_claim, snapshot) is False
assert await finish_export(session, current_claim, snapshot) is True
assert exported.tenant == tenant_at_enqueue
assert imported.id != exported.source_tenant_id
assert imported.domain == replacement.domain
~~~
Also test reclaim after worker interruption, terminal failure after three attempts, no duplicate logical result, rollback of invalid imports and wrong-tenant status/result reads returning 404.

- [ ] **Step 2: Run uv run pytest tests/integration/test_jobs.py tests/integration/test_exports.py -q and observe missing behaviour failures.**
- [ ] **Step 3: Implement migration and job repository.** Keep leases and payload/results in PostgreSQL. Completion/failure performs a conditional update using the full claim identity. A poll may reap an expired final-attempt lease into failed; do not leave it running forever.
- [ ] **Step 4: Implement export/import and the worker.** Export validated M1 tenant metadata only. Worker command mockazure worker --config PATH polls with a bounded idle delay and exits cleanly on interruption. No OAuth/cloud credentials or network calls are needed.
- [ ] **Step 5: Add tenant export ID, tenant import FILE and job show TENANT_ID JOB_ID CLI commands.** Export can wait with an explicit timeout or return the job ID; file output must be an explicit path. Imports reject extra sensitive fields rather than silently retaining them.
- [ ] **Step 6: Run focused tests plus two worker processes against live PostgreSQL.** Stop one after claiming a job, let its lease expire, and verify another finishes exactly one result. Recreate API/worker processes and retrieve the same export.
- [ ] **Step 7: Commit:** feat: add durable tenant export jobs and import.

## Task 6: Docker/native parity and milestone qualification

**Files:** Create Dockerfile, compose.yaml, .dockerignore, tools/verify_foundation.py, tests/acceptance/test_distribution.py, README.md, docs/foundation.md and docs/capabilities.md; finalize compose.test.yaml and configuration initialization.

**Interfaces:**

- Compose services: db, migrate (one-shot), api, worker; test variant adds test runner without changing application code.
- initialize_compose(project_dir: Path, *, api_port: int = 8443, db_port: int = 55432) -> InstanceFiles in bootstrap.py. mockazure init --compose --project-dir PATH calls it to generate reusable database credentials, .runtime/compose.env, and native/container instance configuration.
- PostgreSQL 18 binds project .runtime/postgres to /var/lib/postgresql, using the image's versioned PGDATA location. Do not use the PostgreSQL 17 mount path.
- Shared instance credentials/certificates/config bind from .runtime/instance. Containers use a container-specific config file generated from the same initialization settings; native config addresses localhost while container config addresses db.
- Host API port: 127.0.0.1:8443. Native-development DB port: 127.0.0.1:55432. Ports are configurable to avoid collisions.
- Bootstrap generates a random local database password into an ignored file; PostgreSQL reads it with POSTGRES_PASSWORD_FILE. Never commit usable passwords or print full rendered Compose configuration containing them.
- Application containers connect to an internal Compose network. Dependency downloads/image builds happen before isolated tests.
- tools/verify_foundation.py --config PATH --report PATH exits 0 only when required HTTP, persistence, worker and distribution checks pass.

Use a dedicated .runtime/test-postgres mount and separate Compose project name for tests. Never use compose down -v or recursive filesystem deletion on unverified paths. Any test cleanup resolves an absolute path and verifies it remains within the intended project test directory. Windows bind-mount permission or performance problems must be reported; do not silently relocate data to an unrelated Docker volume.

- [ ] **Step 1: Write failing acceptance assertions and the verification report schema.** Require:
~~~python
assert report["tls_verified"] is True
assert report["tenant_survived_recreate"] is True
assert report["ca_fingerprint_before"] == report["ca_fingerprint_after"]
assert report["admin_token_unchanged"] is True
assert report["two_api_processes"] is True
assert report["two_workers_no_duplicate_exports"] is True
assert report["external_service_requests"] == 0
assert report["wheel_install_passed"] is True
~~~
The report contains outcomes and public fingerprints, never raw secrets or token hashes.

- [ ] **Step 2: Run the new acceptance tests and record the missing deployment behaviour.** Infrastructure acquisition failures are prerequisites to resolve, not successful expected test failures.
- [ ] **Step 3: Implement image and Compose startup.** Build the wheel once, install it into the runtime image, configure health/readiness and a migration dependency. The worker waits for the completed schema. Pin base images by resolved digest. Ensure packaged Alembic resources are present.
- [ ] **Step 4: Implement the verifier and network isolation.** Use real HTTPS with CA verification, two API processes on separate local ports, and two workers sharing the database. Block nonlocal network use in native tests; enforce isolation at the container network layer for container tests. Assert attempted external calls are failures, not successes that happen to return no data.
- [ ] **Step 5: Document both startup paths.** Include PowerShell and Git Bash commands, project-local cache/data paths, configuration/token/certificate filenames, explicit certificate trust instructions, migrations, export/import, backup requirements and troubleshooting. A fresh Docker-only user must be able to build the image, run its init --compose command with the project mounted at /project, and then start Compose without installing Python/uv on Windows. Generate relative data_dir paths so native config is valid on the host and container config is valid at its mounted location. Native users run the same initialization via the installed CLI. Explain that M1 provider APIs are not yet implemented.
- [ ] **Step 6: Execute final verification from the project root.**
~~~text
uv sync --locked
uv run ruff check .
uv run pytest tests/unit tests/integration -q
uv build
docker compose --env-file .runtime/compose.env build
docker compose --env-file .runtime/compose.env up -d
uv run python tools/verify_foundation.py --config .runtime/instance/native.json --report work/foundation-report.json
uv run pytest tests/acceptance -q
git diff --check
~~~
Generate .runtime/compose.env as part of init using the final paths/ports; no source-project env file is read. The verifier includes a controlled database container recreation using the same bind mount, then confirms state. Use a clean wheel environment for package verification rather than an editable install.

- [ ] **Step 7: Inspect the diff and tracked file list, commit and push.** Confirm no .runtime/, work/, credentials or unrelated files are tracked. Commit message: feat: ship verified Docker and native foundation. Push the branch using authenticated GitHub access without exposing a token.

## Self-review and requirement coverage

| Spec requirement | M1 task or explicit later owner |
| --- | --- |
| Independent repo, safe import, Apache-2.0 | Task 1 |
| Project-local state and TLS | Task 2 |
| PostgreSQL, durable tenant data, bounded queries | Task 3 |
| Separate authenticated administration and native CLI | Task 4 |
| Persistent work, leases, retries, idempotence | Task 5 |
| Docker/native parity, restart and worker tests | Task 6 |
| App-only/delegated Microsoft authentication and client adapters | M2; no false M1 implementation claim |
| Stateful Graph, provider permissions/policies and query/delta semantics | M3 |
| Exchange, UAL and generated provider evidence | M4 |
| ARM/RBAC/Activity Logs | M5 |
| Hawk/MES complete release gates and large datasets | M2–M5 incremental tests; M6 release qualification |
| Scenarios, historical seeding and optional ground truth | Added with provider/fixture milestones; not required for M1 startup |
| Full resource reset and complete backup tooling | Follow resource schemas in later milestones; M1 documents limitations |

Self-review checks before handoff:

- Every cross-task type/function above has one defining task.
- Every Review Focus item has a named test owner.
- Each task produces a working deliverable and has a bounded verification command.
- Only approved source blobs may be copied; no runtime/test operation writes to the source project.
- Provider-family implementation is deferred explicitly, not represented by empty success.
- This plan is intentionally limited to M1; later milestones receive their own exact implementation plans.

## Execution handoff

Review this concrete plan before implementation. Choose either:

- Native execution: the main agent implements each task in this session, followed by a fresh whole-branch review.
- Subagent-driven execution: a fresh implementer and reviewer handle each task before continuing.

Recommended for M1: native execution. The six tasks share tightly coupled settings, repository, HTTP and worker interfaces; sequential implementation keeps that context together. Preserve any execution preference the user supplies. Do not begin execution until this written plan has been reviewed and an execution method selected.

## Primary implementation references

- [SQLAlchemy PostgreSQL/psycopg dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)
- [FastAPI application lifespan](https://fastapi.tiangolo.com/advanced/events/)
- [Official PostgreSQL image and PostgreSQL 18 storage path](https://hub.docker.com/_/postgres)
- [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/)
