# Connected Content Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution remains selected. Complete A1–A6 in order using checkbox tracking.

**Goal:** Populate a coherent synthetic organization and execute finite local business workflows.

**Architecture:** Reuse stable population namespaces, canonical service repositories,
local HTTP adapters and durable runs. Add service-ID mappings, deterministic content
assets and focused collaboration mutations; no autonomous simulation engine.

**Tech Stack:** Existing Python/PostgreSQL/worker/console stack and standard-library
content generation.

**Spec:** [Approved design](../specs/2026-10-20-living-tenant-design.md), sections 2–4, 7–9.

## Global constraints

All [program constraints](2026-10-20-living-tenant.md#global-constraints) apply. “Importing
an event cannot grant access or pretend an API call occurred.” No always-on scheduler.
Preserve older profile schemaVersion 1 and generator behavior. Do not use remote
content generators or copy external documents into the corpus.

## Review focus

- Resumed namespaced bindings cannot resolve another tenant's object (A1).
- A document's attachment copy must have the bytes/hash the profile declares (A2/A3).
- An imported Teams member row alone cannot authorize a public write (A4).
- Corporate-looking HTML/documents must remain inert in console previews (A2/A6).
- A lost send response must not become an automatic duplicate send (A6).

## File boundaries

New `population/organization.py` constructs logical relationships;
`population/bindings.py` resolves service identities; `population/corpus/` renders
bytes; `population/generators/business.py` builds service-specific mutations.
Public Teams changes live in `workloads/team_mutations.py` and a separate Graph
mutation router, not in generators. Finite activity is an extension of population.
Paths below are repository-relative; newly created packages include `__init__.py`.

## A1. Contract inventory and namespace-bound organization identities

**Files:** Create `src/mockazure/population/{organization,bindings}.py`,
`src/mockazure/living_tenant/{contracts,models}.py`,
`src/mockazure/living_tenant/data/operations.json`,
`tools/acquire_living_tenant_contracts.py`,
`tests/unit/test_living_tenant_contracts.py`,
`tests/unit/test_organization_generation.py`,
`tests/integration/test_organization_bindings.py`.
Modify `population/{models,planner}.py` and add migration suffix
`organization_bindings` only for persistence missing from existing ResourceBinding.

**Interfaces:** `OrganizationSpec(users, departments, projects, locations, days,
seed, reference_time)` is a strict Pydantic model; users 1–100,000, departments
1–100, projects 1–1,000, locations 1–100, days 1–3,660.
`entity_at(spec, tenant_id: UUID, namespace: str, kind: str, index: int) ->
LogicalEntity(key, kind, properties, relationships)` constructs one indexed entity
without loading the organization. Relationship targets are logical keys.
`EntityBinding(tenant_id, namespace, logical_key, service, actual_id, parent_id,
generation)` is an immutable view over durable bindings.
`bind(session, binding: EntityBinding) -> EntityBinding` rejects conflicting reuse;
`resolve(session, tenant_id, namespace, logical_key, service) -> EntityBinding`
fails on missing/stale bindings.
`OperationContract(key, method, path, request_type, response_type, permissions,
role_constraints, entitlement_constraints, source_refs, supported_variants,
limitations)` is loaded by `living_tenant.contracts.load_operation(key)`.

- [ ] **Tests:** `test_seed_is_chunk_independent` compares logical entities generated
  in batches of 1/100/1,000; `test_manager_graph_is_acyclic` checks every manager path;
  `test_bindings_are_tenant_namespace_and_service_scoped` requires an incorrect
  namespace/service to fail; `test_binding_conflict_does_not_retarget_resume` leaves
  the original mapping unchanged. Missing parents and cross-tenant targets fail.
  Core assertions after constructing those cases:
  ```python
  assert whole == chunks_of_100 == chunks_of_1000
  assert resolved.actual_id == original.actual_id
  assert resolved.tenant_id == tenant_id
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_living_tenant_contracts.py tests/unit/test_organization_generation.py tests/integration/test_organization_bindings.py -q`.
- [ ] **Implement inventory:** Pin operation documentation plus existing v1.0 CSDL,
  source hashes and licenses. Tool accepts `--verify-only` and optional
  `--area content|evidence|identity|hunting`; runtime never acquires sources.
  Inventory existing supported operations first, marking reuse/new/fixture.
  Include the exact additions in A4/C1–C5/D2 and the operation families in the spec;
  absence of an official schema is recorded, with strict sourced local validation.
- [ ] **Implement mappings:** Use stable IDs for imports and actual response IDs for
  public creates. Keep directory deviceId, Intune ID and Defender ID distinct.
  Preserve current ResourceBinding readers; extend with a focused mapping table if
  multiple services cannot be represented without changing old semantics.
- [ ] **Verify:** RED command passes; offline source hash verification passes;
  `tests/unit/test_population_generators.py` and
  `tests/integration/test_population_relationships.py` stay green.
- [ ] **Commit:** `feat: add connected organization and service identity bindings`.

## A2. Deterministic downloadable business corpus

**Files:** Create `src/mockazure/population/corpus/{models,render,text,office,pdf}.py`,
`src/mockazure/population/corpus/data/themes.json`,
`tests/unit/test_business_corpus.py`,
`tests/integration/test_business_content_downloads.py`.
Modify `population/adapters/content.py` and `population/generators/recipes.py`
to feed existing finalized content storage without inline large payloads.

**Interfaces:** `ContentSpec(logical_key, format, theme, seed, revision, fields)`
is strict; format is `txt|csv|json|pdf|docx|xlsx|pptx`.
`RenderedContent(media_type, filename, length, sha256, chunks)` contains a restartable
chunk iterator factory; `render(spec: ContentSpec) -> RenderedContent`.
Bound a rendered document to 8 MiB; larger opaque files use the existing streaming
content generator. Format metadata and ZIP timestamps are fixed by input, not wall time.

- [ ] **Tests:** `test_render_is_byte_identical` compares rerenders and hashes for all
  seven formats; `test_office_parts_and_relationships` parses ZIP/XML content and
  content types; `test_pdf_offsets_and_unicode` checks valid text/font encoding;
  `test_csv_json_preserve_unicode` round-trips content. Include two revisions and
  empty optional fields. Integration download tests require exact byte/hash equality.
  ```python
  assert first_bytes == second_bytes == downloaded_bytes
  assert hashlib.sha256(downloaded_bytes).hexdigest() == rendered.sha256
  assert len(downloaded_bytes) == rendered.length
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_business_corpus.py tests/integration/test_business_content_downloads.py -q`.
- [ ] **Implement:** Standard-library structured formats and small valid Office
  Open XML packages; a bounded PDF renderer using bundled licensed font assets if
  needed for Unicode. Put licenses in NOTICE/package assets. If a rendering library
  is needed, add and lock only that focused dependency.
  Themes cover finance, HR, engineering, sales, operations and support.
- [ ] **Integrate:** Store through existing content finalization/chunking; add no
  arbitrary filesystem or URL reads to profiles. Downloads retain attachment,
  nosniff and CSP protections. Browser preview remains text/metadata only.
- [ ] **Verify:** RED command passes; independently open a representative PDF and each
  Office file using an available parser/viewer to catch self-consistent malformed
  output. Record that check; do not require Office as a runtime dependency.
- [ ] **Commit:** `feat: add deterministic business document corpus`.

## A3. Connected mail, calendar, SharePoint and Teams history

**Files:** Create `population/generators/{business,business_mail,business_files,business_teams}.py`
under `src/mockazure`, `tests/unit/test_business_relationships.py`,
`tests/integration/test_connected_collaboration.py`.
Modify generator registry, existing mail/workload/content/relationship adapters and
`src/mockazure/profiles/data/data/` definitions for the collaboration layers.

**Interfaces:** Preserve `generate(name, parameters, refs, *, offset, limit) ->
list[Mutation]`. New generator names begin `business.` and carry their own version.
Consume A1 bindings and A2 content; historical import steps never call public send
and never claim native-operation provenance.

- [ ] **Tests:** `test_conversation_has_matching_sender_recipient_copies`,
  `test_invitation_links_attendee_and_organizer_events`,
  `test_attachment_bytes_match_document_revision`,
  `test_channel_files_folder_resolves_canonical_drive_item`, and
  `test_namespace_replay_preserves_counts_and_ids`. Assert generated members cannot
  see private/shared-channel content without operational membership/ACL authority.
  ```python
  assert attachment_bytes == document_revision_bytes
  assert after_replay_ids == before_replay_ids
  assert channel_folder["id"] == bound_folder_id
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_business_relationships.py tests/integration/test_connected_collaboration.py -q`.
- [ ] **Implement mail/calendar:** Folders, headers, thread identities, MIME,
  attachments, sent/received copies, recurring meetings, responses and exceptions.
  Use existing fixture/domain import paths; add typed adapters only where missing.
  Conversation IDs are service relationships, not a reused universal ID.
- [ ] **Implement collaboration history:** Projects map to sites/libraries/folders,
  versioned documents and explicit ACLs. Teams/chats contain members, threads,
  mentions and local file references. Keep valid edit/deletion history; no dangling
  author, parent or hosted-content references.
- [ ] **Verify:** New suites pass along with existing population mail/workload and
  graph workflow suites. Public GET/MIME/content reads recover seeded records and
  hashes. Larger generation stays batch-bounded.
- [ ] **Commit:** `feat: generate connected collaboration history`.

## A4. Operational Teams collaboration and missing content actions

**Files:** Create `src/mockazure/workloads/{team_mutations,team_write_access}.py`,
`src/mockazure/providers/graph/team_mutation_routes.py`,
`tests/integration/test_teams_mutations.py`,
`tests/integration/test_teams_write_permissions.py`.
Modify existing Graph router registration, `workloads/{teams,models,repository,facts}.py`,
`population/adapters/workloads.py`, and operation contracts.
Use a migration only if existing revisioned workload rows cannot represent required
native membership/message state.

**Interfaces:** `apply_team_operation(session, actor: PrincipalContext,
operation_key: str, scope_id: str, path_ids: dict[str,str], payload: dict,
wall_now: datetime) -> dict | None`; called only after route contract validation.
Use service-specific permission evaluation and existing scope locks/revisions.

**Operation set:** Graph v1.0 `POST /chats`; member collection POST and member
GET/PATCH/DELETE for `/teams/{team}`, `/teams/{team}/channels/{channel}` and
`/chats/{chat}`; channel message/reply POST and chat message POST; message PATCH
and `softDelete`/`undoSoftDelete` only on the route variants and fields documented
for ordinary collaboration. Pin every variant separately. Existing team/channel
provisioning can remain a lab fixture.

Audit existing mail send/reply/forward, calendar actions, drive upload/download,
move/copy/delete/versions and sharing APIs against A3 recipes. Extend only missing
operations necessary to complete those declared workflows, with entries in A1's
manifest and tests in `tests/integration/test_connected_content_actions.py`.

- [ ] **Tests:** `test_member_change_changes_current_visibility`,
  `test_private_channel_membership_is_not_team_membership`,
  `test_ordinary_send_requires_supported_authority`,
  `test_message_author_and_edit_rules`,
  `test_soft_delete_and_restore_preserve_thread_identity`. Every case uses a
  positive actor and a negative app/delegated actor. Migration-only permissions
  never enable an ordinary send.
  ```python
  assert denied_send.status_code == 403
  assert sent["from"]["user"]["id"] == delegated_sender_id
  assert collected_reply["replyToId"] == parent_message_id
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/integration/test_teams_mutations.py tests/integration/test_teams_write_permissions.py tests/integration/test_connected_content_actions.py -q`.
- [ ] **Implement:** Apply documented permission/role distinctions per operation,
  including separately supported RSC permissions; preserve current read snapshots.
  Validate ownership and foreign IDs under the same scope lock as the change.
  Persist native source facts using existing workload patterns for Phase B.
- [ ] **Connect recipes:** Add exact local public actions with named identities,
  response binding and readback. Unsupported payload fields fail explicitly.
  Content imports remain labeled fixture operations.
- [ ] **Verify:** New suites and existing Teams boundaries/review suites pass; original
  Graph client performs member change → send/reply → collect → delete/readback.
  Add public operations to COVERAGE.md and catalog with accurate limitations.
- [ ] **Commit:** `feat: support operational Teams collaboration workflows`.

## A5. Device, Azure and reusable organization profiles

**Files:** Create `src/mockazure/population/generators/business_estate.py`,
`tests/integration/test_connected_estate.py`,
`tests/unit/test_living_tenant_profiles.py`.
Modify generator registry and `profiles/data/{tenants,data,templates}/`;
extend existing ARM, Storage, Key Vault and monitor adapters where recipe wiring
is missing, not by bypassing their service authorization.

**Interfaces:** Existing generator and adapter protocols. Add layers
`organization-projects`, `business-mail-calendar`, `business-sharepoint-teams`,
`business-device-estate`, `business-azure-operations`; templates
`living-small-business`, `living-department`, `living-enterprise`.
Defaults: 20/250/2,500 users, 7/14/30 history days respectively; all templates
remain parameterized. These are dataset presets, not performance claims.

- [ ] **Tests:** `test_directory_intune_device_link_is_explicit`,
  `test_observed_compliance_does_not_execute_policy`,
  `test_resource_owners_and_diagnostic_destinations_resolve`,
  `test_four_storage_planes_round_trip`, `test_secret_versions_are_redacted`,
  and `test_all_templates_resolve_without_copying_base_layers`.
  ```python
  assert managed_device["azureADDeviceId"] == directory_device["deviceId"]
  assert blob_bytes == file_bytes == original_bytes
  assert secret_value not in json.dumps(manifest)
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/unit/test_living_tenant_profiles.py tests/integration/test_connected_estate.py -q`.
- [ ] **Implement:** Generate owned devices/software/configuration/status evidence,
  applications, subscriptions, groups, compute/network resources and data stores.
  Provision native access/diagnostic settings through existing supported actions.
  Exercise Blob/File/Table/Queue and secret lifecycle through actual local clients.
- [ ] **Compose:** Templates stack named reusable layers with explicit duplicate-key
  overrides. Keep configurable content volume independent from user count, and
  dependency steps ordered. Operational account/secret keys stay out of exports.
- [ ] **Verify:** Resolve all three templates offline; apply small template and verify
  public collection plus content bytes. Existing built-in templates remain unchanged.
- [ ] **Commit:** `feat: add connected device and Azure business profiles`.

## A6. Finite activity batches, console controls and phase qualification

**Files:** Create `src/mockazure/population/{activity,activity_models}.py`,
`tests/integration/test_activity_batches.py`,
`tests/browser/test_living_tenant_console.py`,
`tools/verify_living_tenant.py`.
Modify `population/{planner,runner,repository}.py`, `commands/populate.py`,
`console/static/{profiles,runs,resources}.js`, profile recipes and docs.
Add migration suffix `population_activity` for frozen activity metadata if needed.

**Interfaces:** `ActivityBatch(action_count: int, pacing_ms: int)` is an optional
frozen `RunRequest.activity` field; action count 1–100,000, pacing 0–60,000 ms.
`action_at(snapshot: dict, ordinal: int, refs: dict[str,str]) -> Mutation` returns
one action from the selected recipe without expanding 100,000 profile steps.
Default absent activity preserves historical population behavior. Job scheduling
uses persisted available_at rather than sleeping while holding a lease.

- [ ] **Tests:** `test_batch_finishes_at_exact_count`,
  `test_cancel_stops_future_actions_without_undo`,
  `test_resume_preserves_recipe_and_clock`,
  `test_uncertain_send_requires_attention_not_duplicate`,
  `test_pacing_releases_worker_lease`,
  `test_evidence_time_does_not_change_oauth_expiry`.
  Browser tests verify profile stacking, preview, count/pacing, progress, cancel,
  related-object navigation and visible fixture/native labels.
  ```python
  assert completed_actions == requested_action_count
  assert uncertain_run.state == "needs-attention"
  assert sent_message_count_after_resume == sent_message_count_before_resume
  ```
- [ ] **Observe RED:** Run `python -m pytest tests/integration/test_activity_batches.py tests/browser/test_living_tenant_console.py -q`.
- [ ] **Implement:** Reuse receipt/reconciliation and existing identity references.
  Token acquisition is fresh when needed; public mutation preconditions remain
  effective. Display partial results rather than calling a cancelled run complete.
  No continuous scheduling or fault-injection switches.
- [ ] **Qualify:** `python -m tools.verify_living_tenant --config PATH --phase content --report work/living-content.json`
  runs original local Graph/Exchange/Azure clients. The tool accepts `--phase
  content|evidence|identity|hunting|all`, `--config`, `--report`, `--scale
  small|large` and `--distribution native|docker`. Only implemented phase choices
  work; others fail explicitly until their tasks land. Pin references and compare
  received IDs, relationships and hashes rather than response status alone.
- [ ] **Verify/document:** Run new suites and existing population/console recovery;
  package assets, update profile/deployment guides and record content-phase results.
- [ ] **Commit:** `feat: add finite business activity and console controls`.
