# Graph mail calendar and change notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Native execution is already selected. Steps use checkboxes; implement in this session.

**Goal:** Make schema-conformant Graph v1.0 application workflows produce durable local state, delivery and notifications.
**Architecture:** Extend existing mail/content/delta stores and add focused calendar/subscription modules. Each operation has pinned CSDL/OpenAPI plus REST semantics and uses current delegated/application authorization.

**Tech Stack:** Python 3.12+, FastAPI, SQLAlchemy/psycopg, PostgreSQL 18, Docker; official Python, PowerShell, .NET and JavaScript/TypeScript clients.
**Spec:** [Approved cloud workflows specification](../specs/2026-10-18-cloud-workflows-design.md).
**Program:** [Execution index and shared acceptance contract](2026-10-18-cloud-workflows.md).
**Status:** E1-E4 implemented, reviewed, and locally qualified through all four original client ecosystems at runtime dc40b79. Named E regression130PASS and harness16PASS; [evidence](../../qualification/cloud-phase-e.md). GitHub CI remains unverified; F and final G gates remain pending.

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

- Action input shapes and empty success bodies must not be inferred from resource schemas (E1).
- Repeated accepted sends must follow documented idempotency behavior, not silently duplicate or suppress mail (E1).
- Recurring events spanning daylight-saving transitions must retain occurrence identity and correct times (E3).
- Subscription renewal/revocation racing with delivery must not leak a notification (E4).
- Validation/redirect/DNS behavior must keep webhook delivery on registered local receivers (E4).

## File boundaries and contracts

Graph operations target v1.0 only. Preserve existing beta behavior without advertising additions there. Extend `tools/extract_graph_schema.py` and existing operation-contract machinery. Contract tests must validate requests, responses, discriminators, status/headers and resource-specific annotations.

Authoritative source starting points: [sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0), [event delta](https://learn.microsoft.com/en-us/graph/api/event-delta?view=graph-rest-1.0), [subscriptions](https://learn.microsoft.com/en-us/graph/api/resources/subscription?view=graph-rest-1.0). Stable calendarView delta is scoped to the documented primary-calendar routes, not invented for every calendar.

## Task E1: Drafts send reply forward and attachments

**Files**

- Create: `src/mockazure/mail/send.py`, `src/mockazure/mail/send_models.py`, `src/mockazure/mail/attachment_uploads.py`, `src/mockazure/providers/graph/mail_action_routes.py`, `integrations/contracts/cloud/graph-workflows.json`, `tests/unit/test_graph_workflow_contracts.py`, `tests/integration/test_graph_send_workflows.py`.
- Modify: `src/mockazure/mail/message_mutations.py`, `src/mockazure/mail/content_models.py`, `src/mockazure/mail/authorization.py`, `src/mockazure/jobs/dispatch.py`, `src/mockazure/admin/app.py`, `tools/extract_graph_schema.py`, `src/mockazure/providers/graph/permissions.py`, `src/mockazure/authorization/catalog.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async accept_mail_action(session: AsyncSession, actor: PrincipalContext, mailbox_id: UUID, action: str, message_id: str | None, body: dict | bytes, now: datetime) -> MutationResult`; `async process_mail_delivery(database: Database, claim: JobClaim, wall_now: datetime) -> JobResult`. Consume A2 `require_send_identity`; attachments use shared content and explicit public upload-session rules.

- [x] **Step 1 — Add the named failing scenarios.** test_sendmail_wire_and_local_delivery checks JSON and base64 MIME input, empty 202, saved Sent Items and delivered local recipient copy after a worker cycle. test_draft_reply_forward verifies references/recipients/attachments and supported send identities. test_attachment_upload_contract tests content ranges, expiry, size limits and upload-session authority. Do not add generic idempotency guarantees absent from the operation contract.

```python
assert accepted.status_code == 202 and accepted.content == b''
assert delivered_attachment_hash == original_hash
assert sender_sent_item_count == expected_sent_items
assert unauthorized_send.status_code == 403
```

- [x] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/unit/test_graph_workflow_contracts.py tests/integration/test_graph_send_workflows.py tests/integration/test_mail_content_delta.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [x] **Step 3 — Implement the declared interfaces.** Pin each draft/send/reply/replyAll/forward/createReply/createReplyAll/createForward/attachment operation and allowed aliases/permissions. Implement exact action and MIME wire handling, durable delivery, local recipient outcome and audit/message-delta publication. Preserve recipient permissions and mailbox scopes; store no invented Graph fields. Add source-schema closure tests, rollback/fencing, restart and failures before claiming SDK support.
- [x] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [x] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add schema-conformant Graph mail workflows"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task E2: Calendar resources events and meeting responses

**Files**

- Create: `src/mockazure/calendar/models.py`, `src/mockazure/calendar/events.py`, `src/mockazure/calendar/meetings.py`, `src/mockazure/providers/graph/calendar_routes.py`, `tests/integration/test_graph_calendar_workflows.py`.
- Modify: `src/mockazure/admin/app.py`, `src/mockazure/jobs/dispatch.py`, `src/mockazure/cloud/lifecycle.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async mutate_event(session: AsyncSession, actor: PrincipalContext, owner_id: UUID, calendar_id: str, event_id: str | None, operation: str, body: dict, now: datetime) -> dict | None`; `async process_meeting_delivery(database: Database, claim: JobClaim, wall_now: datetime) -> JobResult`. Use E1 delivery infrastructure for local meeting messages without conflating event identity with message identity.

- [x] **Step 1 — Add the named failing scenarios.** test_calendar_meeting_roundtrip creates calendar groups/calendars/events, updates and deletes, invites a local attendee and processes accept/tentativelyAccept/decline/cancel. test_calendar_permissions enforces read/basic-read/write distinctions, organizer ownership and application versus delegated aliases. Pin transactionId handling and response headers.

```python
assert organizer_event['id'] != attendee_event['id']
assert attendee_status == expected_response_status
assert calendar_owner_unchanged is True
assert invalid_owner_mutation.status_code == expected_permission_error
```

- [x] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_graph_calendar_workflows.py tests/unit/test_graph_workflow_contracts.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [x] **Step 3 — Implement the declared interfaces.** Acquire calendar/event/action schemas and exact operation mappings, including list/get/update/delete for groups/calendars/events. Add permission-aware canonical calendar storage, organizer/attendee relationships and durable meeting delivery. Emit events only into documented audit/notification mappings. Integrate tenant lifecycle from first storage migration.
- [x] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [x] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add Graph calendar and meeting workflows"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task E3: Recurrence time zones availability and delta

**Files**

- Create: `src/mockazure/calendar/recurrence.py`, `src/mockazure/calendar/views.py`, `src/mockazure/calendar/delta.py`, `tests/unit/test_calendar_recurrence.py`, `tests/integration/test_graph_calendar_views.py`.
- Modify: `src/mockazure/providers/graph/calendar_routes.py`, `src/mockazure/calendar/events.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `expand_occurrences(series: dict, start: datetime, end: datetime, limit: int) -> list[dict]`; `async read_calendar_view(session: AsyncSession, actor: PrincipalContext, owner_id: UUID, query: dict, now: datetime) -> dict`; `async read_calendar_delta(session: AsyncSession, actor: PrincipalContext, owner_id: UUID, query: dict, now: datetime) -> dict`. Recurrence owns supported pattern/range parsing, time-zone mappings and stable occurrence/exception identity.

- [x] **Step 1 — Add the named failing scenarios.** test_calendar_dst_and_exceptions crosses spring/fall time transitions, changes a single occurrence, cancels one, changes the series and compares calendarView/instances. test_calendar_delta_reauthorization resumes a bound date-window cursor after restart, rejects another scope/window and rechecks permissions. getSchedule must show only the authorized availability shape.

```python
assert occurrence_ids == expected_stable_ids
assert occurrence_instants == expected_utc_instants
assert len(window_ids) == len(set(window_ids))
assert revoked_delta.status_code == 403
```

- [x] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/unit/test_calendar_recurrence.py tests/integration/test_graph_calendar_views.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [x] **Step 3 — Implement the declared interfaces.** Implement all approved documented recurrence pattern/range forms and sourced Windows/IANA mappings using a pinned, licensed time-zone dependency/data source if stdlib is insufficient; validate Windows and Linux equivalence. Bound expansion with explicit errors, never silent truncation. Add calendarView/instances/getSchedule and only documented v1.0 delta paths/options with current-access checks and tombstones. Test malformed/ambiguous local times against the selected documented profile.
- [x] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [x] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add calendar recurrence availability and delta"`. Preserve unrelated work. Push/review according to the program's phase PR rules.

## Task E4: Subscriptions and local notification delivery

**Files**

- Create: `src/mockazure/notifications/models.py`, `src/mockazure/notifications/subscriptions.py`, `src/mockazure/notifications/delivery.py`, `src/mockazure/notifications/receivers.py`, `src/mockazure/providers/graph/subscription_routes.py`, `tests/integration/test_graph_notifications.py`, `integrations/local_webhook_receiver.py`.
- Modify: `src/mockazure/admin/app.py`, `src/mockazure/jobs/dispatch.py`, `src/mockazure/cloud/lifecycle.py`, `src/mockazure/config.py`.
- Include this task's catalog/reference closure, numbered domain migration if state changes, lifecycle hooks and COVERAGE rows as required by the program index.

**Interfaces:** Produce `async mutate_subscription(session: AsyncSession, actor: PrincipalContext, subscription_id: str | None, operation: str, body: dict, now: datetime) -> dict | None`; `async process_notification(database: Database, claim: JobClaim, wall_now: datetime) -> JobResult`; `resolve_receiver(url: str, configured_receivers: dict) -> dict`. Receiver validation returns only pre-registered local target bindings; never arbitrary public resolution.

- [x] **Step 1 — Add the named failing scenarios.** test_subscription_validation_lifecycle performs exact validationToken response handshake, renew/get/list/delete/reauthorize, expiry and local lifecycle delivery for supported combinations. test_notification_retry_and_revoke retries a failed receiver after restart and stops revoked/expired subscriptions. test_receiver_boundary rejects unregistered hosts, redirect escape and DNS changes; rich resource-data requests reject explicitly.

```python
assert receiver_validation_body == expected_validation_token
assert notification['clientState'] == registered_client_state
assert delivered_source_ids == expected_source_ids
assert external_requests == []
assert post_revocation_deliveries == 0
```

- [x] **Step 2 — Prove the missing behavior.** Run `uv run python -m pytest tests/integration/test_graph_notifications.py tests/unit/test_graph_workflow_contracts.py -q`. Expect the named new scenario to fail for absent behavior; diagnose setup failures separately. Existing regression tests must not be weakened.
- [x] **Step 3 — Implement the declared interfaces.** Pin per-resource/changeType/expiration/lifecycle rules for supported mail/calendar/drive subscriptions. Implement documented notification envelopes and retry behavior, durable outbox/fences and explicit current authorization boundaries. At-least-once deliveries may repeat; never claim exactly-once callbacks. Keep rich encrypted notifications deferred as approved. Add lifecycle cleanup/export and four-ecosystem workflows via G1; update coverage by resource combination.
- [x] **Step 4 — Verify the task.** Rerun the exact pytest command above and require PASS, then perform the index's lint/diff, lifecycle, source-schema and applicable phase-client checks. Record actual counts/client versions and operation-level limitations; never mark unrun checks passing.
- [x] **Step 5 — Commit.** Stage only this task's explicit paths plus its owned catalog/migration/lifecycle/coverage changes. Run `git diff --cached --check` and inspect staged names, then `git commit -m "feat: add durable local Graph subscriptions"`. Preserve unrelated work. Push/review according to the program's phase PR rules.
