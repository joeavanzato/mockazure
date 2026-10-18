# Coverage phase 3 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.
> Native execution is selected. Steps use checkboxes; no implementation agents.

**Goal:** Collect persistent SharePoint, OneDrive, Teams, Intune and Management Activity evidence.
**Architecture:** Workload-specific adapters share revisioned metadata and phase 2 binary
storage. The activity feed is a separate authorized service over durable audit publication.
**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker, PowerShell.
**Spec:** [Approved program design](../specs/2026-10-17-coverage-expansion-design.md).
**Status:** Implemented and qualified in PRs #19–#22. The checklist below preserves the original execution plan.

Current implementation and qualification evidence: [coverage results](../../coverage-expansion-results.md).

## Global Constraints

- Work only in MockAzure; never modify VTW or copy its secrets, generated files or untracked files.
- No real-tenant traffic; use official public Microsoft documentation and pinned schema sources.
- Native execution; commit cohesive changes, push reviewable PRs, do not auto-merge.
- Graph additions target v1.0 contracts; do not infer beta support from a shared router.
- No host trust, DNS or firewall changes; all generated service/content URLs stay local.
- Current COVERAGE.md describes shipped behavior; planned features remain separately labeled.
- Preserve existing data and accepted collector limitations; no silent destructive migration.

## Review Focus

- Encoded site/item/chat IDs: preserve identity without allowing path traversal (3A/3B).
- Selected permissions or RSC without resource grants: deny instead of widening access (3A/3B).
- Expired download capability or changed ACL: enforce the documented five-minute boundary (3A).
- Intune policy assignments without device observations: never infer compliance (3C).
- Late audit events and publisher/client overlap: publication-time paging is stable and isolated (3D).

## Shared interfaces established in 3A

Create `src/mockazure/workloads/{models,repository,imports,cursors}.py`. Models contain
WorkloadScope(tenant, workload, scope, revision), versioned typed resource payloads,
versioned relationships, cursor rows and authoritative grants. Data keys include tenant,
workload, kind, public ID and scope; use indexed extracted query columns for hot fields.
Do not make a payload dictionary the authorization source of truth.

`WorkloadImport` in imports.py contains `scope: str`, `expected_revision: int | None`,
`records: list[WorkloadRecord]`, `edges: list[WorkloadEdge]`, `tombstones: list[WorkloadKey]`.
Each record has `kind: str`, `id: str`, `payload: dict`, optional `content_id: UUID`;
keys and edges use explicit kind/ID pairs. Workload-specific schemas validate payloads.
`ImportResult` has `revision: int`, `count: int`.
`async import_workload(session, tenant_id: UUID, workload: str, batch: WorkloadImport)
-> ImportResult` commits metadata atomically, checks references and preserves history.
Expose POST `/_mock/tenants/{tenant}/workloads/{workload}/import` for admin use only.

`async list_versions(session, tenant_id: UUID, workload: str, scope: str,
kind: str, revision: int, query: CompiledQuery) -> list[dict]` returns bounded rows.
Define `CompiledQuery` in workloads/repository.py as a frozen dataclass containing SQL
predicate, ordering, projection and limit, built only by trusted workload adapters.
Authorization predicates are mandatory; raw client SQL/column names are never accepted.
Reuse existing typed query AST/compiler where compatible. Workload adapters own public
serialization, operation permissions, cursor payloads and Microsoft error translation.

Reuse phase 2 `ContentRef`/`stream_content`; phase 1 `enforce_read_budget` for Graph.
Append operation contracts and CSDL type closure per workload, including derived types.
Each PR modifies `src/mockazure/admin/app.py`, `src/mockazure/authorization/catalog.py`,
source/schema manifests, reset/export/maintenance integration and COVERAGE.md as needed.
Migration names below receive the next available numeric Alembic prefix in sequence.

### Task 3A: SharePoint and OneDrive

**Files:** Shared workloads modules above; create
`src/mockazure/workloads/{sites,drives,drive_delta,resource_access}.py`,
`src/mockazure/providers/graph/{site_routes,drive_routes}.py`,
`src/mockazure/admin/workload_routes.py`, migration `site_drive_evidence.py`,
`tests/unit/test_site_drive_contracts.py`, `tests/integration/test_site_drive_evidence.py`,
`test_drive_content_delta.py`, `integrations/powershell/Test-SiteDriveEvidence.ps1`.

**Interfaces:** `async authorize_site(session, context, site_id: str, operation: str) -> None`
in resource_access.py combines OAuth authority and applicable site/resource grants.
`async resolve_drive_item(session, tenant_id: UUID, drive_id: str, identifier: str,
by_path: bool = False) -> dict` in drives.py resolves canonical item metadata.
`async issue_download(session, context, content_id: UUID, now: datetime) -> str` returns
an authorized signed local capability expiring after five minutes. Download verification
checks tenant/content/expiry; an already issued URL survives ACL revocation until expiry,
unless its content is removed. Document this deliberate capability behavior.

- [ ] Pin official list/get routes for site root/ID/hostname:path/search, site drives/lists/items
      and fields; user/group/site drive aliases, root/item/path/children, permissions, versions,
      content and root delta. Pin site permission list/item/create/delete authority separately.
- [ ] Add failing tests for composite/encoded IDs, path ambiguity, selected-site grant absence,
      delegated ACL restrictions, hidden unauthorized pages, imported permission evidence not
      becoming a grant, signed URL expiry, byte ranges and permission revocation on new issuance.
- [ ] Run `uv run pytest tests/unit/test_site_drive_contracts.py tests/integration/test_site_drive_evidence.py tests/integration/test_drive_content_delta.py -q` red.
- [ ] Implement shared versioned imports/cursors and site/list/drive adapters. Preserve dynamic
      list fields under the official fieldValueSet schema. Site permission writes update the
      authoritative selected-app grant store and audit facts; import display rows stay separate.
      Support Sites.Selected at site scope first; finer list/item selected scopes remain explicit
      gaps. Do not confuse site grants with user sharing permissions on drive items.
- [ ] Implement streamed /content redirects, versions and drive-root delta. Test changes/removals,
      stable paging across rename/move/import, latest checkpoints, reauthorization and restart.
      Metadata/content import drives changes; public upload/edit/sharing APIs remain unsupported.
- [ ] Implement reset/export/restore/prune and upgrade; qualify official SDK and PowerShell
      discovering a site, walking a document library, downloading every blob and reading delta.
      Update coverage and source contracts, run existing regression/CI gates and publish 3A.

### Task 3B: Teams and chats

**Files:** Create `src/mockazure/workloads/{teams,team_access}.py`,
`src/mockazure/providers/graph/team_routes.py`, migration `teams_evidence.py`,
`tests/unit/test_teams_contracts.py`, `tests/integration/test_teams_evidence.py`,
`integrations/powershell/Test-TeamsEvidence.ps1`.

**Interfaces:** `async authorize_conversation(session, context, conversation_kind: str,
conversation_id: str, operation: str) -> None` in team_access.py. Team/channel/chat identities
and membership edges stay separate. `teams.py` uses list_versions and ContentRef; filesFolder
references canonical drive items through resolve_drive_item, not duplicate file metadata.

- [ ] Pin v1.0 team GET, user joinedTeams, channels/members, channel messages/replies,
      chats/chat members/messages, hostedContents and raw hosted content, filesFolder.
      Record operation-specific delegated, tenant-wide app and resource-specific permissions;
      supported query options differ between chat, channel and hosted-content operations.
- [ ] Add failing tests for private-channel isolation, shared-channel explicit local membership,
      app RSC limited to one team/chat, nested reply paging, edited/deleted message observations,
      opaque IDs, hosted-content bytes, filesFolder permission checks and revoked membership.
- [ ] Run `uv run pytest tests/unit/test_teams_contracts.py tests/integration/test_teams_evidence.py -q` red.
- [ ] Implement typed imports, authoritative local membership/RSC setup and read routes.
      Preserve message bodies/mentions/attachments/replies without fetching links. Tenant-wide
      app permissions and RSC use their documented independent authority paths; do not require
      an application to impersonate a team member. Delegated users obey membership visibility.
- [ ] Add lifecycle and restart/429/batch tests; qualify official clients following team/channel
      and chat collection workflows through all pages. Mark meetings/calls/sending/federation
      and extra export APIs unsupported. Update coverage and publish 3B after regression gates.

### Task 3C: Intune inventory, configuration and observed compliance

**Files:** Create `src/mockazure/workloads/{intune,intune_contracts}.py`,
`src/mockazure/providers/graph/intune_routes.py`, migration `intune_evidence.py`,
`tests/unit/test_intune_contracts.py`, `tests/integration/test_intune_evidence.py`,
`integrations/powershell/Test-IntuneEvidence.ps1`.

**Interfaces:** `async device_observation(session, tenant_id: UUID,
directory_device_id: UUID) -> DeviceObservation | None` in intune.py returns frozen
`DeviceObservation(device_id: UUID, platform: str | None, compliant: bool | None,
observed_at: datetime)` defined in intune_contracts.py. Map managedDevice azureADDeviceId
to the directory device's public deviceId, not its object ID; reject ambiguous authoritative
mappings. This supplies phase 4 policy evaluation but never trusts client headers.

- [ ] Pin managedDevices list/item and user navigation, detectedApps and managedDevices
      relationships, deviceConfigurations/deviceCompliancePolicies list/item/assignments/
      deviceStatuses. Initial derived configurations: windows10GeneralConfiguration,
      iosGeneralDeviceConfiguration, androidGeneralDeviceConfiguration,
      macOSGeneralDeviceConfiguration; compliance policies: windows10CompliancePolicy,
      iosCompliancePolicy, androidCompliancePolicy, macOSCompliancePolicy. Validate each
      against pinned v1.0 CSDL; unlisted derived types fail import explicitly.
- [ ] Add failing tests for absent active Intune entitlement, Graph permission families,
      managedDevice/directory-device identity mismatch, no compliance from assignments,
      unknown compliance, full status paging, typed platform payloads and deleted observations.
- [ ] Run `uv run pytest tests/unit/test_intune_contracts.py tests/integration/test_intune_evidence.py -q` red.
- [ ] Implement licensed typed imports and read adapters; persist compliance as observed state,
      separate from policy assignment intent. Configuration and inventory use their distinct
      DeviceManagement permission requirements. Add device_observation with explicit unknowns.
- [ ] Add lifecycle, upgrade, restart and least-permission SDK/PowerShell checks; use no destructive
      device actions. Update coverage and publish 3C after regression gates.

### Task 3D: Office 365 Management Activity pull feed

**Files:** Create `src/mockazure/activity/{models,contracts,subscriptions,publication,routes}.py`,
`src/mockazure/admin/activity_routes.py`, migration `activity_feed.py`,
`tests/unit/test_activity_contracts.py`, `tests/integration/test_activity_feed.py`,
`test_activity_publication.py`, `tools/verify_activity_feed.py`.
Modify `src/mockazure/endpoints.py`, `src/mockazure/authorization/catalog.py`,
`src/mockazure/identity/tokens.py` only where service registration requires it,
`src/mockazure/jobs/dispatch.py` and existing evidence publication integration.

**Interfaces:** EndpointProfile.activity defaults to the existing local origin, independently
configurable. Register the canonical Management Activity resource audience and permission IDs
from official references; aliases must not make a shared local origin select the wrong service.
`async publish_activity(session, tenant_id: UUID, record_ids: list[UUID],
published_at: datetime) -> list[str]` in publication.py makes immutable content blobs with
stable IDs; uniqueness keys make retries idempotent. Inputs reference existing auditLogRecord
facts; project auditData into feed schema without the Graph result wrapper. Explicit feed-only
imports use validated audit records through the admin route. Original event IDs remain stable.

- [ ] Pin feed subscription/content/error schemas and service permissions from official
      Management API docs. Implement all five content types with separate DLP permission checks.
- [ ] Add failing tests for wrong Graph audience, tenant path mismatch, client-isolated start/stop,
      repeated start, disabled subscription, publication versus event time, seven-day expiry,
      24-hour windows, NextPageUri continuity, DLP permission denial, 429/Retry-After and
      worker retry without duplicate blobs. Assert webhook config is rejected rather than ignored.
- [ ] Run `uv run pytest tests/unit/test_activity_contracts.py tests/integration/test_activity_feed.py tests/integration/test_activity_publication.py -q` red.
- [ ] Implement subscriptions and durable publication using existing leased jobs/outbox patterns.
      Content list uses publication time and stable snapshot pages; content GET returns immutable
      arrays with service-shaped errors. Add deterministic local publication delay/failure controls
      through admin configuration for tests, never Microsoft outbound requests.
- [ ] Extend endpoint-profile loading/export with backward-compatible defaults. Verify old saved
      profiles and existing Graph/Exchange/ARM token audiences unchanged. Add lifecycle/export
      restoration, worker restart and subscription revocation tests.
- [ ] Qualify raw local HTTP start -> list -> retrieve all content -> stop -> denied retrieval,
      using app tokens acquired through the actual local issuer. Correlate native records across
      feed and Graph UAL by original audit identity while preserving service-specific wrappers.
      Update coverage, run full CI/default checks and publish 3D.

## Completion evidence

Each PR adds source-pinned operation records, exact route/query/permission coverage and
at least one nonempty full collection workflow. Run the targeted commands red then green,
`uv run ruff check .`, unit tests and applicable PostgreSQL regressions. Use the existing
isolated test database setup, never a live runtime database. Add small workload smoke checks
to CI; record large-data measurements separately. Phase 3 is complete only when all four
workloads plus the feed have successful qualified workflows and preserved known limits.
