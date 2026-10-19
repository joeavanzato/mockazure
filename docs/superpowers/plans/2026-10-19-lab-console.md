# MockAzure Lab Profiles and Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Native execution was previously selected and remains selected. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a documented shared lab with composable tenant/data templates, a reproducible population CLI/script, and a service-oriented browser console.

**Architecture:** Package a pure profile resolver, explicit per-service adapters and a durable population runner inside the existing Python application. The CLI and same-origin browser use the same management APIs; public service actions retain normal identity and permission enforcement. Reuse PostgreSQL and existing workers, with no separate frontend server or queue broker.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, SQLAlchemy/psycopg, PostgreSQL 18, Typer, packaged HTML/CSS/JavaScript and Docker. Add a locked PyYAML dependency for declarative input; use Python's standard password-hashing primitives. Browser automation is test-only.

**Spec:** [Approved lab-console specification](../specs/2026-10-19-lab-console-design.md), approved by the user on 2026-10-19.

**Status:** Written plan awaiting user review. This document adds no product implementation. Execute natively after plan approval; no new execution-method question is necessary.

## Global Constraints

- Never connect execution to a real Microsoft tenant. Do not modify VTW or import its untracked files, secrets or generated certificates.
- Preserve PostgreSQL, Docker and native Python deployment, Windows/PowerShell support, Apache licensing and the existing Microsoft contract constraints.
- Later layers override explicitly supplied properties. Conflicting definitions of the same resource require an explicit override.
- Lists replace as a whole; overlays do not implicitly delete previously generated resources.
- Set an eight-hour absolute session expiry. Existing bearer-token CLI administration continues to work.
- The database inspector is disabled by default; there is no arbitrary SQL, writes or unrestricted database export.
- No new queue broker or frontend server. Runtime operation must not require Node, a CDN or network access.
- No individual accounts, SSO or new role system. All shared administrators have the same management access.
- Existing source baseline is `68a6d10dcda27c836201495f0bbfd6277afa22c1`; do not assume it has been merged. Preserve qualified branches and runtime volumes. No auto-merges.
- Commit cohesive changes frequently and push reviewable work. Tests and report claims bind to the actual tested commit. Existing billing-blocked CI is not a passing CI result.
- All paths below are repository-relative. New Python packages include `__init__.py`. Allocate migrations after the reconciled Alembic head, currently `073_monitor_diagnostics`; retain the descriptive suffixes below.
- Graph payloads use pinned v1.0 contracts. New public operations require official Microsoft operation references; management metadata must not enter Microsoft payloads.

## Review Focus

- A teammate changes data between preview and apply: reject affected stale state without overwriting it (Tasks 2/3).
- A worker loses its response after a non-idempotent action: reconcile or require attention instead of silently duplicating it (Task 3).
- A crafted profile or uploaded content contains YAML expansion, path escapes, external URLs or active HTML: reject unsafe input or render inertly (Tasks 1/2/4).
- Shared browser sessions must never act as Graph/ARM/data-plane tokens or expose control-plane secrets (Task 4).
- Large tenants must not trigger whole-export reads, destructive queue consumption or unbounded browser/server memory use (Tasks 2/4/5).

## Execution shape and source reconciliation

Five substantive tasks, in order, each with its own red/green cycle and reviewable commits. Keep one implementation branch/PR if review size remains practical; split only if a task needs independent integration. The tasks share interfaces and are not five user-owned chats.

At the start of Task 1, fetch origin, inspect the remote main and cloud stack, and identify a base containing all approved cloud features plus this design and plan. If main already contains that content, use it; otherwise base the new feature branch on the existing complete stack and state the dependency in the PR. Do not reset the root checkout or merge existing PRs. Record base SHA in the execution notes. Check applicable AGENTS instructions and preserve untracked files. The existing isolated checkout is usable; do not install a second environment unnecessarily.

For Python checks use `.venv/Scripts/python.exe -X utf8 -m pytest` and `.venv/Scripts/ruff.exe check` on Windows. The equivalent `uv run` form is acceptable after the lock/environment is synchronized. Integration tests use the existing `tests/integration/conftest.py` isolated-database fixture; set `MOCKAZURE_TEST_DB_PORT` to an owned test PostgreSQL port and keep its password in `.runtime/test-db.password`. Never print it. Use disposable lab project names/ports for runtime tests and preserve unrelated Docker networks/volumes.

## File and interface map

| Area | Files and responsibility |
| --- | --- |
| Profiles | `src/mockazure/profiles/{models,loader,resolver,identity}.py`: bounded declarative input, inheritance, references, provenance, deterministic IDs |
| Population adapters | `src/mockazure/population/{types,catalog,transport}.py`, `adapters/*.py`, `generators/*.py`: service capabilities, paging, intended mutations, generation and readback |
| Runs | `src/mockazure/population/{models,repository,planner,runner,verification}.py`: frozen previews, checkpoints, durable execution and verification |
| Management API/CLI | `src/mockazure/admin/{profile_routes,population_routes,resource_routes}.py`, `src/mockazure/commands/{profiles,populate}.py` |
| Console security | `src/mockazure/console/{models,passwords,sessions,boundary,inspector,routes}.py`, `src/mockazure/commands/console.py` |
| Browser | `src/mockazure/console/static/`: index, CSS and focused ES modules; no third-party runtime downloads |
| Packaged examples | `src/mockazure/profiles/data/`: reusable tenant/data definitions and four compositions; `examples/populate.py`: thin CLI entry wrapper |
| Documentation | `docs/getting-started.md`, `docs/profiles.md`, current README/index/coverage updates and measured lab-console results |

## Task 1: Versioned profile composition and offline validation

**Deliverable:** A packaged, independently tested resolver and `mockazure profile validate/resolve` commands. No lab state is changed.

**Files:** Create `src/mockazure/profiles/{models,loader,resolver,identity}.py`, `src/mockazure/commands/profiles.py`, `tests/unit/test_profile_loader.py`, `tests/unit/test_profile_resolution.py`, `tests/unit/test_profile_cli.py`. Modify `src/mockazure/cli.py`, `pyproject.toml`, `uv.lock`.

**Interfaces:**

- `load_bundle(documents: dict[str, bytes]) -> ProfileBundle` in `loader.py`. Bundle keys are logical document names, not filesystem paths.
- `resolve_template(bundle: ProfileBundle, template: str, overrides: dict[str, JsonValue], reference_time: datetime) -> ResolvedProfile` in `resolver.py`; use `pydantic.JsonValue`, timezone-aware datetimes and strict models with unknown metadata fields forbidden.
- `stable_id(tenant_id: UUID, namespace: str, logical_key: str, seed: int) -> UUID` in `identity.py` using a fixed versioned UUID namespace.
- Models in `models.py`: `ParameterDefinition(type, default, minimum, maximum, shared)`, `ProfileDocument(schemaVersion, kind, name, extends, parameterDefinitions, parameters, resources, steps, tenantProfile, dataProfiles)`, `ResourceDefinition(family, properties, override)`, `StepDefinition(mode, family, operation, target, parameters, dependsOn, override)`, and `ResolvedProfile(schemaVersion, template, parameters, resources, steps, origins, referenceTime, seed, digest)`. Fields valid only for the applicable kind are enforced. Resource/step collections are maps keyed by logical name; `mode` is `fixture` or `service`.
- Input parameter names declared shared (including the spec example's `seed`, `referenceTime` and `days`) normalize to `shared.<name>` internally. Other values normalize to `<profile-name>.<name>`; ambiguous unqualified overrides fail. Preserve the spec's concise YAML spelling for shared parameters.
- `ProfileBundle` contains named validated documents. `origins` maps resolved JSON pointers to source document/layer. `digest` hashes canonical normalized data, not YAML formatting. References are typed objects: `{"$param":"shared.seed"}` or `{"$ref":"users.analyst"}`; no string interpolation language. Symbolic resource references remain typed until target binding in Task 3.

- [ ] **Step 1 — Reconcile the source and write failing tests.** Record baseline as above, then add fixtures for parent/child tenant and template inheritance plus two overlapping data profiles. Exact assertions include:

```python
assert resolved.parameters["shared.users"] == 2500  # run override wins
assert resolved.resources["analyst"].properties["displayName"] == "Analyst"
assert resolved.resources["analyst"].properties["tags"] == ["new"]
assert resolved.digest == json_equivalent.digest
assert stable_id(tenant, "lab", "analyst", 42) == stable_id(tenant, "lab", "analyst", 42)
assert stable_id(tenant, "lab", "analyst", 42) != stable_id(other_tenant, "lab", "analyst", 42)
```

Add named tests `test_conflicting_resource_requires_override`, `test_null_is_not_delete`, `test_missing_reference_and_inheritance_cycle_fail`, `test_dependency_does_not_install_profile`, `test_unsafe_yaml_duplicate_keys_aliases_and_paths_fail`, `test_parameter_namespace_and_origin`. Exceptions must identify source/key without echoing sensitive values. Reject duplicate keys in JSON as well as YAML.

- [ ] **Step 2 — Run the new tests and establish failure.** Run `python -m pytest tests/unit/test_profile_loader.py tests/unit/test_profile_resolution.py tests/unit/test_profile_cli.py -q` using the environment above. Expect absent interfaces/behavior, not infrastructure failure.
- [ ] **Step 3 — Implement bounded parsing and composition.** Enforce 1 MiB/document, 16 MiB/bundle, 128 documents, maximum depth 32 and 100,000 parsed nodes in total. Reject YAML aliases/anchors/merge keys and custom tags before construction; keep ISO timestamps as strings until typed validation. Use an explicit safe-loader subclass and duplicate-key handling. Shared values follow spec precedence; namespaced values cannot leak between profiles. Topologically order steps with stable source order for ties. Preserve nullable payload values; service-specific validation remains Task 2. Add offline `profile validate --bundle PATH` and `profile resolve --bundle PATH --template NAME --parameters FILE --output FILE`; CLI paths are local input selected by the caller, never server reads. Resolve `now` exactly once before hashing.
- [ ] **Step 4 — Verify.** Rerun Step 2 to PASS, run changed-path Ruff and `git diff --check`. Verify existing CLI help/version tests remain passing. No full regression rerun for this pure component unless dependency changes reveal a broader issue.
- [ ] **Step 5 — Commit.** Stage only Task 1 files and commit `feat: add composable tenant and data profiles`.

## Task 2: Service catalog, adapters and connected starter data

**Deliverable:** A service catalog with bounded browsing and validated provisioning, deterministic generators and reusable starter profiles. It can execute a small batch under test before durable orchestration is added.

**Files:** Create `src/mockazure/population/{types,catalog,transport}.py`; `src/mockazure/population/adapters/{directory,security,evidence,mail,workloads,activity,arm,storage,keyvault,monitor}.py`; `src/mockazure/population/generators/{directory,evidence,mail,workloads,cloud}.py`; `src/mockazure/profiles/data/{tenants,data,templates}/` with named definitions below; `src/mockazure/admin/resource_routes.py`; `tests/unit/test_population_catalog.py`, `tests/unit/test_population_generators.py`, `tests/integration/test_population_adapters.py`, `tests/integration/test_resource_browser_api.py`. Modify `src/mockazure/admin/app.py` and relevant existing domain/administration modules only where an adapter needs a validated, bounded operation. Reuse `tools/scale_dataset.py`, `tools/cloud_management_fixtures.py` and `tools/intune_fixtures.py` by extracting pure construction helpers, leaving existing verification entry points compatible.

**Interfaces (defined in `types.py`):**

- `FamilyDescriptor(key, service, display_fields, relationships, operations, contract_refs, limitations)` and `OperationDescriptor(key, mode, method, path_template, api_version, input_schema, identity_required, destructive, replay_policy)`; replay policy is `idempotent`, `reconcilable` or `manual`.
- `AdapterContext` carries `Database`, `Settings`, tenant UUID and an optional server-resolved identity reference; it never contains user-supplied target URLs. `ResourceQuery(limit, continuation, filters, parent)` defaults to 100 and caps at 500. `ResourcePage(items, continuation)` contains sanitized summaries.
- `Mutation(operation, logical_key, payload, expected_version, identity_ref)`; `Inspection(actual_id, version, exists, conflicts)`; `BatchResult(items, continuation, effects)`; `VerificationResult(status, expected, actual, errors)`.
- `ServiceAdapter` protocol: `describe() -> FamilyDescriptor`, `validate(mutation: Mutation) -> None`, `async inspect(ctx, mutation) -> Inspection`, `async browse(ctx, query: ResourceQuery) -> ResourcePage`, `async apply(ctx, mutations: list[Mutation]) -> BatchResult`, `async verify(ctx, result: BatchResult) -> VerificationResult`. Implementations use domain repositories for fixture operations and local HTTPS public APIs for `service` operations; no direct arbitrary SQL mutation path.
- `generate(name: str, parameters: dict[str, JsonValue], refs: dict[str, str], *, offset: int, limit: int) -> list[Mutation]` in each generator module. `catalog.get_adapter(family: str) -> ServiceAdapter` and `catalog.list_families() -> list[FamilyDescriptor]` are the shared lookup API.

**Required examples:** tenant profiles `small-business` and `midsize-business` (extends small); data layers `baseline-business-activity`, `security-evidence`, `exchange-mail-and-audits`, `sharepoint-and-teams`, `intune-inventory`, `azure-inventory`, `storage-content`, `keyvault-secrets`, `logs-and-diagnostics`, `activity-feed`, `suspicious-signins`, `volume-signins`; templates `developer-lab`, `collection-lab`, `investigation-lab`, `volume-lab`. Names and family support are recorded in a machine-readable catalog fixture so omissions fail tests. Ordinary sample profiles default to 20 users/7 days; volume size is explicit. Include a mail/calendar service-action example and an Azure storage/diagnostic service-action example, not only historical imports.

- [ ] **Step 1 — Write failing catalog/generator and adapter tests.** Parametrize one fixture import/provision-and-readback test per family in the spec's nine-row matrix. Add tests for unsupported actions, typed Graph v1.0 rejection, cross-tenant references, stale versions, preserved unrelated records and byte-identical content round trips. Include these assertions:

```python
assert first_batch == regenerate_same_seed_time_and_offset
assert other_namespace_ids.isdisjoint(first_namespace_ids)
assert unknown_graph_field.status_code == 422
assert insufficient_public_permission.status_code == 403
assert peek_before == peek_after  # browsing a queue does not consume it
assert unrelated_resource_after == unrelated_resource_before
assert page.continuation is not None and len(page.items) <= 500
```

Add `test_catalog_inventory_has_no_silent_missing_family`, `test_preview_of_replace_names_complete_affected_scope`, `test_rejects_foreign_endpoints_and_redirects`, `test_content_upload_is_not_server_path_read`, and `test_large_browse_does_not_call_export`. Include security credentials in fixture storage to prove summary serialization excludes them.

- [ ] **Step 2 — Establish failure.** Run the four new test files above. Missing catalog/adapters must fail; unsupported branches must not be skipped and counted as covered.
- [ ] **Step 3 — Implement adapters and resource APIs.** Add authenticated `GET /_mock/resources/catalog`, `GET /_mock/tenants/{tenant}/resources/{family}`, `POST .../{family}/preview`, and `POST .../{family}/apply`. Fetch detail via a typed identifier query, avoiding lossy path encoding for ARM/Storage IDs. Apply requires the preview fingerprint plus relevant version checks; return 409 on conflict. Reuse content upload/chunk/download routes rather than base64-embedding files in JSON. Honor each existing import's smaller byte/record limits. A replacement-only backend must get a genuine per-resource validated operation or stay explicitly replacement-only; never export/merge/replace a whole tenant for a single add. Add supported form schemas and JSON validation metadata from the appropriate operation contracts. Filter continuations bind tenant/family/query and follow bounded keyset/provider paging. Forward neither browser credentials nor arbitrary headers to public adapters; use explicit test identity resolution and configured endpoints with redirects disabled. Package built-ins and reusable pure generators.
- [ ] **Step 4 — Verify.** Rerun the new tests to PASS and existing tests for each changed domain. Record each catalog operation's mode, path, reference and readback test; every required family must have a demonstrated browse path and declared provisioning behavior. Run coverage-reference validation using the existing tooling where public contracts change, plus Ruff/diff checks.
- [ ] **Step 5 — Commit by coherent service group.** Use `feat: add lab resource catalog and population adapters` and `feat: add reusable connected lab data profiles`; do not batch unrelated fixture changes or generated secrets into commits.

## Task 3: Durable population runs, shared API, CLI and script

**Deliverable:** Users can preview, apply, inspect, cancel and safely resume a stacked template from the packaged CLI, with persisted runs and public readback verification.

**Files:** Create `src/mockazure/population/{models,repository,planner,runner,verification}.py`, `src/mockazure/admin/{profile_routes,population_routes}.py`, `src/mockazure/commands/populate.py`, `examples/populate.py`, migrations with suffix `population_runs`, `tests/integration/test_population_runs.py`, `tests/integration/test_population_recovery.py`, `tests/unit/test_populate_cli.py`. Modify `src/mockazure/commands/profiles.py`, `src/mockazure/cli.py`, `src/mockazure/admin/app.py`, `src/mockazure/db/alembic/env.py`, `src/mockazure/jobs/dispatch.py` and existing job claim logic only as needed for fencing compatibility.

**Interfaces:**

- `RunRequest(bundle_id, template, target, dataset_namespace, parameters, identity_refs)` and `Target(existing_tenant_id | create_tenant)` are strict discriminated models in `planner.py`; exactly one target form is required. Reserve intended UUID for a new tenant in preview without creating a tenant row.
- `async preview(database: Database, request: RunRequest, now: datetime) -> Preview` returns ID, resolved digest, target UUID, state preconditions, mutation summaries/counts, at most 10 samples/family, warnings and expiry. Preview expires after one hour; it grants no write authority.
- `async submit_run(database: Database, preview_id: UUID, digest: str, now: datetime) -> RunView` in `repository.py`; creates any new tenant and durable job atomically once, validates state preconditions and rejects stale/expired previews.
- `async process_population(database: Database, claim: JobClaim, wall_now: datetime) -> JobResult` in `runner.py`, registered as `population.apply`. Reuse existing `JobClaim`, lease/epoch fencing and worker cycles.
- `RunView(id, tenant_id, state, progress, steps, errors, manifest_digest)` uses states `pending`, `running`, `verifying`, `succeeded`, `failed`, `needs-attention`, `cancelled`. Steps persist stable operation keys, resolved IDs, input digest, expected state and outcome; uncertain executions do not masquerade as failed-safe retries.
- Define `Preview` and `RunView` as strict serializable models in `planner.py` and `models.py` respectively. Large step/resource bindings are paged separately rather than embedded unboundedly in status. Exported manifests include redacted resolved configuration and digests: synthetic Key Vault values and local test identity credentials are excluded along with instance credentials. Protected execution inputs remain available to the worker without being echoed through status/errors.

**API/CLI contract:** `/_mock/profiles` lists/imports bundles; `GET /_mock/profiles/{id}` and revision-checked `PUT` save edits as immutable versions. `POST /_mock/population/previews`, `POST /_mock/population/runs`, `GET /_mock/population/runs[/{id}]`, `POST .../{id}/cancel`, `POST .../{id}/resume`, `GET .../{id}/manifest`, `GET .../{id}/ground-truth`. CLI adds `profile list/import/export`, `populate preview --bundle PATH --template NAME (--tenant-id UUID | --create-tenant FILE) --namespace NAME --parameters FILE --output FILE`, `populate apply --preview-id UUID --digest HASH`, `populate status/cancel/resume/manifest --run-id UUID`. Profile-file export and run-report export refuse unintended file overwrite. `examples/populate.py` only forwards arguments to the packaged CLI.

- [ ] **Step 1 — Write failure/recovery tests.** Cover frozen bundle/version persistence, existing/new tenant targets, same-namespace rerun, separate namespaces, cancel, resume, two workers and two tenants. Interrupt after one acknowledged batch, restart worker, and compare stored IDs/counts. Inject response loss after a non-idempotent action. Assertions:

```python
assert repeated_run.created_count == 0
assert changed_preview_response.status_code == 409
assert old_worker_checkpoint_write is False
assert unknown_action_result.state == "needs-attention"
assert duplicate_mail_send_count == 0
assert active_runs_for_one_tenant == 1
assert unrelated_tenant_can_progress is True
assert cancelled_run.completed_batches_remain_queryable is True
```

Add `test_completed_step_is_rechecked_before_skip`, `test_new_tenant_submit_is_idempotent`, `test_public_readback_permissions_are_not_admin_existence`, `test_profile_redefinition_does_not_change_running_snapshot`, `test_manifest_and_ground_truth_exclude_credentials`, `test_generator_batches_respect_adapter_limits`. Verify symbolic references use captured service IDs, not guessed IDs.
- [ ] **Step 2 — Establish failure.** Run the two integration files and CLI unit file. Confirm missing durable state/commands fail before implementation.
- [ ] **Step 3 — Implement run state and scheduling.** Persist bundle versions, previews, runs, steps, resource bindings and checkpoints in PostgreSQL. Use a durable tenant run slot/fenced ownership so only one run applies to a tenant, without holding an open transaction across HTTP. Yield/requeue bounded work before the existing 30-second job lease expires, or extend only through an owned lease heartbeat. Persist intent before service calls; a database rollback cannot undo a completed HTTP action. Adapters declare reconciliation: stable-ID imports are reconciled; ambiguous non-idempotent operations require attention. Enforce actual state/version checks immediately before mutation. Public operations without conditional-write guarantees are insert-only or explicit replacement with documented races; never advertise atomic conflict protection they lack. Batch cancellation preserves receipts. Use monotonic elapsed-time bounds for publication waits and retain expected/actual readback errors. Do not block one worker waiting for work only that worker can publish: yield verification polls into later cycles. CLI exit status is nonzero for failure/needs-attention/validation errors, and pending is not success when `--wait` is selected.
- [ ] **Step 4 — Verify.** Rerun Step 2 to PASS; run existing job/evidence/notification dispatch regressions. Exercise `developer-lab` via the script and CLI against one disposable local instance, then replay and verify no duplicate generated resources. Verify failed permission checks remain failures. Run migration upgrade on an empty test database and a populated pre-change database; preserve existing tenant data.
- [ ] **Step 5 — Commit.** Commit durable-run state separately from the CLI convenience surface using `feat: add resumable lab population runs` and `feat: expose profile population through CLI and script`.

## Task 4: Shared-lab login and service-oriented browser console

**Deliverable:** A functional packaged console with password login, resource editing, profile composition, runs and an optional read-only inspector. Use frontend-design when implementing the UI, after this plan is approved.

**Files:** Create `src/mockazure/console/{models,passwords,sessions,boundary,inspector,routes}.py`, `src/mockazure/console/static/{index.html,styles.css,app.js,api.js,resources.js,profiles.js,runs.js,inspector.js}`, `src/mockazure/commands/console.py`, migrations with suffix `console_sessions`, `tests/unit/test_console_passwords.py`, `tests/integration/test_console_auth.py`, `tests/integration/test_console_inspector.py`, `tests/browser/test_lab_console.py`. Modify `src/mockazure/admin/app.py`, `src/mockazure/config.py`, `src/mockazure/bootstrap.py`, `src/mockazure/commands/local.py`, `src/mockazure/cli.py`, `src/mockazure/db/alembic/env.py`, `compose.yaml`, `pyproject.toml`, `uv.lock`.

**Interfaces:**

- `hash_password(password: str) -> str`, `verify_password(password: str, encoded: str) -> bool` in `passwords.py`: use `hashlib.scrypt` with random 16-byte salt, `n=32768`, `r=8`, `p=3`, `dklen=32`, `maxmem=67108864`, a versioned encoding and constant-time comparison. Validate availability in installed environments; do not downgrade to a fast hash. Password input accepts 12–1024 UTF-8 bytes and is never truncated.
- `async login(session: AsyncSession, password: str, source: str, now: datetime) -> IssuedSession` and `async authenticate(session, cookie: str, now: datetime) -> ConsoleSession | None` in `sessions.py`. Store the hash of a 32-byte random cookie value, a separate 32-byte random CSRF token, absolute expiry `now + 8 hours`, credential generation and revocation state. The CSRF token is returned only to the authenticated browser session; it cannot authenticate a request by itself. Exclude the session table from inspection/export.
- Define `IssuedSession(cookie, csrf_token, expires_at)` as an internal return model in `sessions.py`, `ConsoleSession` as the persistent model in `models.py`, and `ManagementIdentity(method, session_id)` in `boundary.py`, with method `bearer` or `session`. Login responses set the cookie header rather than returning its value in JSON. The current-session route returns the session's CSRF token after authentication, supporting reloads and multiple tabs without invalidating each other's forms.
- `authorize_management(request: Request) -> ManagementIdentity` in `boundary.py` accepts existing bearer tokens or a valid console session for management only. Invalid explicitly supplied bearer credentials fail rather than falling back to cookies. Cookie-authenticated unsafe methods require exact allowed Origin and the matching `X-MockAzure-CSRF` token.
- `async inspect_table(session: AsyncSession, table_key: str, tenant_id: UUID | None, limit: int, continuation: str | None) -> ResourcePage` in `inspector.py` uses an explicit descriptor allowlist with no arbitrary projections or SQL. Tenant-owned table queries require a tenant; explicitly listed global metadata is labeled instance-wide.

**Routes/configuration:** `POST /_mock/console/login`, `GET /_mock/console/session`, `POST /_mock/console/logout`; static login shell at `/_mock/ui/`; authenticated inspector at `/_mock/console/inspector`. `mockazure console password set --config PATH [--password-file PATH]` prompts without echo if no protected file is supplied, updates the credential generation and revokes existing sessions. Local database/config access remains the administrator recovery path; no unauthenticated setup route. Add `console_enabled` (default true), `console_inspector_enabled` (default false), explicit configured public origin and allowed hosts. An unset password leaves the console locked with setup instructions; it never enables anonymous management.

- [ ] **Step 1 — Add security and browser failing tests.** Test successful login, wrong password, expiry exactly at eight hours, logout, credential rotation, CSRF, bearer compatibility and two API replicas sharing session/throttle state. Login throttle permits at most five failed attempts/source/minute plus 30 failures/instance/minute with timed recovery, returns 429/Retry-After and does not trust arbitrary forwarded source headers. Test cookie flags `Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/_mock`; one cookie name and exact logout path. Assert:

```python
assert login_session_public_graph_request.status_code == 401
assert expired_console_request.status_code == 401
assert mutation_without_csrf.status_code == 403
assert inspector_when_disabled.status_code == 404
assert "admin_token" not in serialized_inspector_rows
assert script_in_resource_name_executes is False
assert queue_message_count_after_browse == queue_message_count_before
```

Add `test_login_cross_origin_rejected`, `test_cookie_does_not_authorize_storage_or_arm`, `test_inspector_denies_nested_secret_columns`, `test_profile_text_is_inert_and_not_server_code`, `test_ui_pagination_does_not_load_all_pages`, and browser smoke tests for template selection/reordering, preview, run progress, creating a resource and public readback.
- [ ] **Step 2 — Establish failure.** Run the new unit/integration files. Stage a test-only locked Playwright Python/browser dependency for the automated browser suite and confirm failure reflects absent UI, not a missing browser installation. Runtime stays independent of Playwright/Node.
- [ ] **Step 3 — Implement login boundary and minimal UI.** Keep existing host validation ahead of exemptions; exempt only exact static asset/login endpoints, never all `/_mock/console`. Login and mutation Origin checks use the explicit public origin, not an untrusted forwarded header. Perform password hashing off the event loop and apply throttle before expensive work. Serve only packaged static assets with a strict content security policy; never execute uploaded HTML or embed untrusted values using `innerHTML`. Avoid localStorage for credentials. Keep UI modules small and use the shared catalog/preview/apply APIs with tenant selection, actionable errors and keyboard navigation. Secret retrieval requires an explicit service operation; summaries stay redacted. Build the inspector with bounded, tenant-filtered SQLAlchemy queries and approved fields, including safe treatment of JSON columns. Add `--bind-address` and `--advertised-host` initialization options for API/data-plane published ports while keeping database publication fixed to loopback; regenerate matching endpoint URLs/certificate SANs without changing internal Docker listeners. Mount credentials/config using existing patterns. Update password/setup instructions in CLI help.
- [ ] **Step 4 — Verify.** Run auth/inspector tests, existing admin/host/config/Compose regressions and browser smoke tests. Inspect the actual UI at desktop width and a narrower viewport: paged tables, long JSON errors, loading/empty states and keyboard focus must remain usable. Exercise login and logout across two local API replicas. Verify a configured LAN hostname works with CA verification from a second local client; do not disable TLS verification. Record the exact shared-access topology tested.
- [ ] **Step 5 — Commit cohesive slices.** Use `feat: add shared lab console authentication`, `feat: add service browser and profile run console`, and `feat: add optional read-only lab inspector` as the corresponding tests pass.

## Task 5: User guide, packaging and complete workflow qualification

**Deliverable:** The requested tested deployment guide and usable distribution, with truthful breadth/limits and repeatable qualification results.

**Files:** Create `docs/getting-started.md`, `docs/profiles.md`, `docs/lab-console-results.md`, `tools/verify_lab_console.py`, `tests/acceptance/test_lab_console_workflows.py`, `tests/unit/test_lab_console_package.py`; modify `README.md`, `docs/index.md`, `COVERAGE.md`, `docs/capabilities.md`, `pyproject.toml`, `.github/workflows/ci.yml`, existing artifact checks as appropriate. Include packaged examples/assets and the thin script in source-distribution paths; built-in definitions and generator functionality must also be present in wheels.

**Interface:** `python -m tools.verify_lab_console --config PATH --profiles developer-lab collection-lab investigation-lab volume-lab --report PATH` performs local-only workflows and writes source SHA/fingerprint, dependency versions, profile digests, case outcomes, counts, timings, limitations and distribution provenance. Output contains no secrets. The report is not a replacement for underlying test assertions.

- [ ] **Step 1 — Write failing acceptance/package checks.** A fresh installed wheel without repository/tools access must list built-ins, resolve a template and serve console assets. Repeat from installed sdist and Docker. Require a catalog-family coverage matrix and exact run/adapter case identifiers, not merely HTTP 200 counts. Test the documented sequence creates related data, the second identical run adds zero duplicates, and a deliberate unsupported action reports a useful error. Check corrupted/incomplete reports cannot pass.

```python
assert installed_profiles >= {"developer-lab", "collection-lab", "investigation-lab", "volume-lab"}
assert required_service_families <= verified_service_families
assert report.failed_cases == []
assert repeated_run.generated_duplicate_count == 0
assert report.source_fingerprint == artifact_source_fingerprint
```

- [ ] **Step 2 — Establish failure.** Run `python -m pytest tests/unit/test_lab_console_package.py tests/acceptance/test_lab_console_workflows.py -q` under explicit local acceptance configuration. Missing packages/data/workflows fail instead of skipping required cells.
- [ ] **Step 3 — Write the guide and qualification runner.** Execute and document PowerShell Docker initialization, password setup, CA trust, one stacked composition through CLI/script and browser, public API readback, additive repeat, resume, reset boundaries, native use and shared-host setup. Link the full profile schema and precedence examples. Include backup/key requirements, limits, TLS/permissions/worker troubleshooting and the difference between fixture provisioning and application actions. Verify 10,000 users and 100,000 evidence rows with bounded batches; count both via paged public APIs, interrupt/resume one run, and show the browser uses bounded pages. Record memory observations without inventing a performance SLA. Run all four small starter templates and per-family adapter checks. Explain that resource inventory/observations do not execute guest compute, real mail delivery, proprietary detection or SMB/NFS. Update `COVERAGE.md` for administration changes without overstating public API qualification.
- [ ] **Step 4 — Final verification and review.** Run the complete unit/integration suite once on the final candidate with the repository's existing opt-in gates appropriately enabled and local fixtures prepared; run the new acceptance and browser suites, artifact checks, Ruff and diff checks. Preserve existing cloud qualification evidence and run focused regression clients for changed public behavior; do not automatically repeat the previous million-record/four-ecosystem program without a relevant change. Record skips/external blockers separately. Follow native execution's final independent branch review, repair actionable issues and rerun affected checks. Commit source before recording its release fingerprint; documentation-only report commits must distinguish code fingerprint from report commit. Push and attach the PR, inspect hosted CI, and never report billing-blocked CI as green. Do not merge.
- [ ] **Step 5 — Commit and hand off.** Use `docs: add deployment and composable profile guide` and `test: qualify packaged lab console workflows`. Provide the guide, profile examples, actual run report, PR link and remaining limitations. Export user-facing review copies to this chat's outputs directory when appropriate.

## Spec coverage and planning self-review

| Spec sections | Owning task |
| --- | --- |
| 1–2 intended outcome/reuse/architecture | Tasks 1–5; no separate service or runtime toolchain |
| 3 inheritance, overlays, IDs, targets | Task 1 resolution; Task 3 target binding |
| 4 preview/apply/verify/recovery | Tasks 2–3 |
| 5 all service families and profiles | Task 2 catalog/examples; Task 5 required coverage matrix |
| 6 resources/profiles/runs/inspector UI | Task 4, using Tasks 1–3 interfaces |
| 7 shared-lab authentication/deployment | Task 4; Task 5 installation/client checks |
| 8 guide and operating procedures | Task 5 |
| 9 acceptance and scope | Tests throughout, consolidated in Task 5 |

Self-review completed: tasks cover every spec section; interface definitions precede use; the five review-focus conditions have named owner tests; no arbitrary HTTP/SQL/plugin runtime is introduced; destructive replacement remains explicit. Implementation may refine internal module splits while preserving these boundaries and contracts. Newly discovered external behavior requirements must be recorded rather than silently expanding scope.

Implementation references: [Python hashlib password-derivation API](https://docs.python.org/3.12/library/hashlib.html#key-derivation) and [PyYAML parser/loader documentation](https://pyyaml.org/wiki/PyYAMLDocumentation). Safe loading alone is not the proposed profile boundary: duplicate-key, alias, node/depth and reference restrictions above are additional application checks. Exact new dependency versions are resolved, reviewed and locked during Task 1; no runtime downloads are permitted.
