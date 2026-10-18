# Coverage phase 2 implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.
> Native execution is selected. Steps use checkboxes; no implementation agents.

**Goal:** Store and collect real local mailbox content, including durable message delta.
**Architecture:** Extend the canonical mail store; add a small PostgreSQL binary-content
service used by this phase and later drive/Teams workloads. Use immutable versions and
folder-scoped change journals rather than deriving content from statistics.
**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker, PowerShell.
**Spec:** [Approved program design](../specs/2026-10-17-coverage-expansion-design.md).
**Status:** Implemented and qualified in PR #18. The checklist below preserves the original execution plan.

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

- Legacy nonzero folder counts with zero messages: preserve statistics-only mode (2A.2).
- Interrupted uploads and database restart: no visible partial content or dangling metadata (2A.1).
- Moving a message during enumeration: snapshot stability and correct folder delta removals (2A.3).
- Cross-mailbox/cross-tenant blob references: no raw-content access through ID guessing (2A.1/2).
- Malformed MIME and reference attachments: preserve bytes, never fetch arbitrary URLs (2A.2).

## Files and interfaces

Create `src/mockazure/content/{models,repository,routes}.py`,
`src/mockazure/mail/{content_models,messages,message_queries,message_delta}.py`,
`src/mockazure/providers/graph/mail_content_routes.py`,
`tests/unit/test_content_contracts.py`, `tests/unit/test_message_contracts.py`,
`tests/integration/test_content_storage.py`, `test_mail_content.py`, `test_mail_content_delta.py`,
`integrations/powershell/Test-MailContent.ps1`, `tools/verify_mail_content.py` and
migration `mail_content.py` with the next available Alembic numeric prefix.
Modify `src/mockazure/mail/models.py`, `src/mockazure/mail/authorization.py`,
`src/mockazure/admin/mail_routes.py`, `src/mockazure/admin/app.py`,
`src/mockazure/jobs/exports.py`, `src/mockazure/commands/maintenance.py`,
`src/mockazure/config.py`, `tools/extract_graph_schema.py`,
`src/mockazure/providers/graph/schemas/v1.0.json` and the phase 1 operation manifest.
Wire lifecycle helpers into existing reset/export services, not only the CLI.

Shared content interfaces in repository.py (async; session is AsyncSession):

- `begin_upload(session, tenant_id: UUID, size: int, sha256: str, media_type: str) -> UUID`
- `put_chunk(session, tenant_id: UUID, upload_id: UUID, ordinal: int, data: bytes) -> None`
- `finalize_upload(session, tenant_id: UUID, upload_id: UUID) -> ContentRef`
- `stream_content(session, tenant_id: UUID, content_id: UUID, start: int = 0, end: int | None = None) -> AsyncIterator[bytes]`

`ContentRef` is a frozen dataclass in content/models.py with `id: UUID`, `size: int`,
`sha256: str`, `media_type: str`; end is exclusive. Public adapters authorize the resource
before calling content storage. Storage always checks tenant ownership. Private admin
upload routes under `/_mock/tenants/{tenant}/content/uploads` return uploadId, accept
PUT `/{upload}/chunks/{ordinal}`, then POST `/{upload}/complete` returns ContentRef.
They require the separate admin token. Limit chunks to 1 MiB, reject conflicting ordinal
retries; same bytes are idempotent. Default max object 256 MiB, configurable.

Mail import extends `/_mock/tenants/{tenant}/mail` under `/content/import` with a typed
batch of folders/messages/attachments/tombstones and explicit mailbox mode. Maximum
1,000 records/8 MiB per batch by default; binary content uses uploaded references.
`async import_mail_content(session, tenant_id: UUID, batch: MailContentImport) -> ImportResult`
in messages.py validates the whole batch and returns `revision: int, count: int`.
Define both Pydantic types in mail/content_models.py. Public IDs remain opaque strings.

### Task 2A.1: transactional binary storage

- [ ] Add `test_chunk_retry_and_finalize_hash`, `test_unfinished_upload_not_readable`,
      `test_cross_tenant_content_denied`, `test_stream_range_boundaries` and
      `test_restart_and_reference_aware_prune`. Assert exact hashes, failed hash does not
      finalize, one-byte boundary ranges, 416 for invalid public ranges, and bounded chunks.
- [ ] Run `uv run pytest tests/unit/test_content_contracts.py tests/integration/test_content_storage.py -q`
      and confirm missing-feature failures.
- [ ] Implement manifest/chunk rows, staged uploads, limits, streaming and private admin
      routes. Finalization is atomic; share only finalized blobs and tenant-local hashes.
      Add export manifests with binary hashes, restore validation, reset and safe pruning.
- [ ] Run the same tests green; exercise a 256 MiB object without a complete in-memory
      buffer. Add upgrade tests and commit the storage portion on the 2A branch.

### Task 2A.2: mailbox collection and raw content

- [ ] Pin v1.0 folder/message/attachment/mailboxSettings operations and official derived
      schemas. Record precise app/delegated permissions (including Mail.ReadBasic field
      restrictions), body preferences, page limits and filter/order restrictions. Include
      /me and folder-scoped aliases in the operation manifest; no implicit beta routing.
- [ ] Add `test_legacy_statistics_mode_preserved`, `test_content_mode_statistics`,
      `test_message_body_permissions`, `test_folder_aliases_and_hidden_paging`,
      `test_attachment_types_and_raw_mime`, `test_mailbox_settings_permission`, and
      `test_invalid_batch_is_atomic`. Assert no synthesized messages, exact raw bytes,
      distinct attachment types, explicit unsupported reference content and no remote fetches.
- [ ] Run `uv run pytest tests/unit/test_message_contracts.py tests/integration/test_mail_content.py -q` red.
- [ ] Implement typed imports and authorization-filtered list/item routes for all Phase 2
      read families. Reuse shared content and existing mailbox authority. MIME remains raw
      source bytes; parse metadata only under explicit import validation, never silently
      regenerate imported MIME. Header/body fields follow $select and Mail.ReadBasic rules.
- [ ] Implement explicit statistics-only to content-backed transition with expected revision;
      derive counts from content only in the new mode. Validate folder trees, parent ownership,
      message/attachment references and existing inbox-rule folder targets. Preserve archive
      statistics without exposing an invented Graph archive mailbox.
- [ ] Extend export/reset/upgrade and run targeted tests plus existing mail/rule/Exchange
      regression tests. Commit the collection portion.

### Task 2A.3: mutations, delta and wire qualification

- [ ] Add `test_move_id_preferences`, `test_copy_allocates_independent_identity`,
      `test_delta_move_and_delete`, `test_delta_restart_two_instances`,
      `test_delta_reauthorizes`, `test_selection_and_received_filter_continuation`.
      Assert normal versus immutable IDs across move, source removal/destination addition,
      final deltaLink, no lost changes after restart, and rejection of tampered tokens.
- [ ] Run `uv run pytest tests/integration/test_mail_content_delta.py -q` red.
- [ ] Implement PATCH isRead/categories, move/copy/delete with revision locking and native
      audit facts. Add folder-scoped delta with supported receivedDateTime ge/gt filtering,
      token-encoded options, tombstones and documented expiry. Never reuse directory-delta
      tokens across mailboxes or invent tenant-wide message delta.
- [ ] Qualify original Graph SDK/PowerShell listing folders, retrieving all message pages,
      downloading MIME/attachments and resuming delta after local mutation and restart.
      Add read-budget checks through phase 1 `enforce_read_budget` and qualified batch routes.
- [ ] Run unit/integration regressions and `tools/verify_mail_content.py`; update COVERAGE.md,
      mail contracts and compatibility with the exact fields/actions supported. Commit, push
      and open/attach the 2A PR; require full CI and default-install checks.

## Handoff to phase 3

Shared content interfaces above are the only cross-workload storage dependency. Mail
retains its own models, authorization and delta semantics. New workloads must not use
mailbox IDs or permissions as generic content authorization. Add the new verification
entry point to CI with a bounded dataset; keep the large scale run a separate report.
