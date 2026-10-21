# MockAzure — acceptance criteria

This checklist defines implementation completion for the redesign. It supplements `DESIGN_SYSTEM_HANDOFF.md` and `reference/ORIGINAL_BRIEF.md`. `MockAzure-Interactive-Mockup.html` and `source/MockAzure-Source-Fragment.html` demonstrate presentation and selected interactions; they do not prove authentication, API fidelity, persistence, or server authorization.

## Test context and evidence rules

Use the repository's test harness and isolated synthetic data. Prepare tenants **A** and **B**, a console administrator session, an API reader permitted to read but denied the tested write, and a writer permitted for that operation. If Hunting is present, prepare a Hunting identity with the real required entitlement and a query returning more than 200 rows when the verified server contract permits that volume. Otherwise use the supported returned-row cap and verify truthful counts/full-returned-result export. These are capabilities, not required hardcoded identity names. Include overlapping record IDs across tenants if the storage model permits and more than 50 browse records. Choose actual catalog operations for tests.

Report each applicable ID as **PASS**, **FAIL**, or **BLOCKED**, with test name/manual procedure and evidence. **N/A** is allowed only for a genuinely absent optional branch/server feature, supported by repository/capability evidence. An omitted prototype interaction or missing required integration is not N/A. A required missing server contract is BLOCKED, with the exact dependency and user impact. A stub, screenshot, or mocked response cannot establish backend completion.

## 1. Deployment, login, shell, and context

| ID | Role and procedure | Required outcome |
|---|---|---|
| BAS-01 | Developer installs and starts the application using existing documented commands. | Local Python/static packaging, or the actual branch's established stack, works without an unsolicited framework/build/deployment migration. Required assets load locally. |
| AUTH-01 | Signed-out user submits missing, incorrect, then correct shared administrator password. | Real server authentication rejects invalid input and establishes the existing session only for valid credentials. Pending/error states are clear. No registration or personal-account flow appears. |
| AUTH-02 | Administrator refreshes, expires the session, and logs out; attempts a protected deep link. | Existing session rules apply; protected requests are denied or redirected appropriately. Logout invalidates the session. Password and secrets are absent from URLs/client storage and displayed diagnostics. |
| AUTH-03 | New operator expands setup help. | Exact command and configuration guidance match the installed repository; no invented CLI syntax or design-preview login is shipped. |
| NAV-01 | Authenticated user enters the console and refreshes tenant context. | Resources is the initial screen. Shell matches handoff hierarchy; tenant selector/refresh, active page, management/API distinction, and required destinations are visible and work. No overview dashboard replaces Resources. |
| NAV-02 | User opens direct screen/record/run links, reloads, then uses Back/Forward. | URL-addressable context resolves under authorization; list/detail selection is coherent. Invalid IDs have recoverable states. History navigation neither reapplies writes nor silently changes their targets. |
| TEN-01 | Administrator loads A, switches to B while A's request is delayed, then returns to A. | B never displays A's late data, previews, selection, query results, content references, or errors as current. Cache/request keys include the relevant tenant and identity. |
| TEN-02 | User tampers with tenant IDs or opens a foreign record/run URL. | Server enforces tenant scope; UI reports the actual outcome and preserves clear context. No client-only tenant filter acts as the security boundary. |

## 2. Resources, details, relationships, and uploads

| ID | Role and procedure | Required outcome |
|---|---|---|
| RES-01 | Administrator visits every service/family in the real catalog. | Friendly discovery covers all supported families, including non-directory services. IDs, service, API path, supported operations, field constraints, and limitations remain accessible. Sample-only families are not advertised as implemented. |
| RES-02 | Administrator loads using valid and invalid scope/parent and JSON filters. | Requests use actual backend contracts. Invalid JSON stays editable with a field error; valid filters affect server loading as documented. Loading, empty, failure, and retry states are distinct. |
| RES-03 | Administrator browses at least 51 records. | Up to six catalog-selected columns, prominent identifiers, and 50-row cursor pages render. Next uses the returned cursor; First resets it. Family/scope/filter/tenant changes reset incompatible cursors. No invented totals. |
| RES-04 | User searches or applies a simple visual filter on one loaded page. | Wording states current-page scope; only loaded records are filtered. Clearing restores the page. No claim of global search/sorting without a verified backend contract. |
| RES-05 | Administrator inspects records from several families and opens Raw JSON. | Accurate structured summary and provenance accompany complete formatted record JSON. IDs/tenant are clear; Prepare an edit and applicable content downloads use real targets/contracts. |
| REL-01 | Where supported, user follows actual membership/mailbox/other relationship links, including a record with no links. | Links use server relationship identifiers and remain tenant-scoped. No department, display name, or naming convention is treated as proof of a relationship. Missing mappings display an honest empty/unavailable state. |
| UPL-01 | Administrator selects files of 16,777,216 and 16,777,217 bytes. | The browser accepts the boundary size subject to other server rules and blocks the oversized file before upload. UI states 16 MiB; server constraints remain authoritative. |
| UPL-02 | Administrator uploads valid content, observes progress/failure, then uses its content ID in an operation. | Real bytes reach the supported endpoint, progress reflects transfer, errors are actionable, and the returned ID is valid for the correct tenant. Replacing the file/content reference invalidates any dependent preview. |

## 3. API and fixture workbench

| ID | Role and procedure | Required outcome |
|---|---|---|
| OP-01 | Administrator opens Add/Edit and every supported operation family. | Reusable catalog/schema-driven controls cover operation, scope, target, scalar fields, parameters, JSON, and content where applicable. Add creates through its real preview/apply path; it is not a shortcut to a sample user edit. |
| OP-02 | Reader executes a permitted read, then attempts the tested write; writer executes the write. | Actual server authorization determines outcomes. Console administration grants no API permission; denied writes are never retried as fixtures/admin. Guidance and required permissions match the selected operation. |
| OP-03 | Administrator switches between API and fixture modes, including through Add's fixture shortcut after API success. | Mode, identity applicability, authorization implications, and target remain explicit. Prior API response, preview, consent, and success/evidence actions clear; fixture mode never presents inherited API success. |
| PRE-01 | Writer/administrator enters a supported mutation and previews it. | No mutation occurs during preview. Actual server preview output identifies affected IDs, field effects, warnings, and supported secondary effects. Use issued preview IDs/tokens/digests/versions where present; verify the actual authoritative binding mechanism. UI distinguishes MockAzure preview from a Microsoft API endpoint. |
| PRE-02 | After preview, change each bound input independently. | Tenant, identity/auth context, mode, family, operation, target, scope, payload/scalar field, parameter, content ID/file, and selection of replacement as the operation each invalidate Apply and consent immediately. A fresh matching preview is required. Checking consent for an unchanged reviewed proposal is a separate gate, not a proposal edit. |
| PRE-03 | Delay preview response, change an input, then release the response. | The late preview cannot restore Apply or display as current. Changing away and back cannot reuse a stale preview contrary to server validity rules. |
| PRE-04 | Tamper with an apply request, revoke permission, and exercise concurrent target changes; test expiry/version mismatches when supplied by the contract. | Server enforces actual authorization and reviewed-context rules. UI explains rejected/conflicting changes and requires re-preview. Client fingerprints do not substitute for server authority. Missing binding/conflict protection is documented as explicit backend work or a blocker, not assumed token support. No false success follows rejection. |
| PRE-05 | Double-click Apply or retry after an uncertain transport result. | Pending controls prevent ordinary duplication; handling follows verified server retry/idempotency semantics. Unknown outcome triggers authoritative reconciliation, not an invented success or blind second mutation. |
| REP-01 | Preview replacement of a fixture containing an optional field; omit that field in the replacement payload. | Apply remains disabled until explicit consent for that preview. Checking consent enables Apply without invalidating the unchanged proposal; unchecking disables it. Confirmed replacement removes/retains fields exactly as the server's replacement contract specifies and never silently behaves as PATCH. |
| OP-04 | Enter malformed JSON, unsupported fields, missing scope, and a server-rejected value. | Errors identify the relevant input and preserve edits. Unsupported operations cannot return canned success. Successful output/status comes from the actual selected operation. |

## 4. Profile composition, previews, and revisions

| ID | Role and procedure | Required outcome |
|---|---|---|
| CMP-01 | Administrator chooses a template, tenant profile, and multiple data layers; adds/removes/reorders layers. | Real catalog choices and supported ordering semantics are retained across Compose/Configure/Review. Tenant profile is selectable, not a hardcoded sample card. Boundary move controls behave correctly. |
| CMP-02 | Configure existing tenant, then new tenant with slug/domain/name, namespace, users, activity days, seed, and additional JSON. | Fields follow real validation and preserve values between steps. Empty/invalid values remain editable. Draft updates and blur do not replace live controls or silently coerce/drop values. |
| CMP-03 | Import JSON and YAML; directly edit source; export; save revision; reload the app and load it. | All supported composition fields, order, parameters, and activity settings round-trip. Revisions persist using the real store. Exports are actual files; invalid/unsupported content is explained, never silently discarded. |
| CMP-04 | Preview a valid composition. | Server resolves actual counts, warnings, bounded samples, digest, destination, and replacement/removal implications. Estimated values are labelled; sample formulas are not claimed as resolved output. No population occurs yet. |
| CMP-05 | Change each composition input after preview, including imported source, layer order, tenant profile, destination/new-tenant fields, seed, extra JSON, and activity settings. | Preview and replacement acknowledgement invalidate immediately. Apply cannot start a run for modified or stale inputs. Async preview races obey PRE-03/PRE-04. |
| CMP-06 | Apply a valid preview for existing and new tenant paths. | Actual server run ID is selected in Runs. New tenant creation follows the real contract; no cloned sample tenant or browser-only population is presented as completed work. |
| ACT-01 | When supported, configure recipe step, finite count, and pacing; test unsupported recipe/invalid bounds. | Real supported bounded activity is validated, persisted in the run snapshot, and verified separately from fixtures. No unbounded generator or fabricated operation evidence. |

## 5. Durable run execution, history, and recovery

| ID | Role and procedure | Required outcome |
|---|---|---|
| RUN-01 | Observe actual run transitions and inspect success/failure/attention examples. | Real state, completed/total steps, Created/Unchanged/Verified counts, technical details, and errors agree with persisted server records. Pending/running/verifying/succeeded/failed/needs-attention render where supported. |
| RUN-02 | Start run A; edit draft composition and inspect historical run B while A remains active; return to A. | A continues independently. Each run retains its immutable resolved configuration, tenant, counters, and polling identity. History selection neither stops A nor rewrites its settings from the current draft. |
| RUN-03 | Navigate away, refresh, disconnect/reconnect, and restart the app while a run is active. | Server persistence recovers the run and recorded progress. Polling is bounded and cleaned up; stale responses cannot overwrite another run. Browser timers do not define execution. |
| RUN-04 | Cancel an active run, including a race with completion. | Correct tenant/run receives the command. UI distinguishes requested from acknowledged cancellation and reconciles final server state. Completed work may remain; no rollback or guaranteed immediate stop is promised. |
| RUN-05 | Resume/recheck a supported stopped or interrupted run. | Real recovery reconciles persisted work under the backend's existing semantics. No fabricated progress, restart from scratch disguised as resume, or unsupported idempotency claim. |
| RUN-06 | Download manifest and ground truth for two different runs. | Actual downloads contain the selected run/tenant/configuration and available results. Fixture counts, action verification, provenance, and evidence time remain distinguishable. A JSON inspection modal alone fails this criterion. |

## 6. Hunting, provenance, and clocks

| ID | Role and procedure | Required outcome |
|---|---|---|
| HNT-01 | When supported, authorized identity executes cookbook and edited custom queries; reader executes the same query. | Backend evaluates supported KQL and denies unauthorized requests. Unsupported syntax/tables/entitlements receive actual errors. Editing cookbook text cannot still return canned cookbook success. |
| HNT-02 | Change tenant, identity, query, cookbook, timespan, or query parameters after results arrive or while pending. | Results become clearly invalid or clear; stale exports are disabled. Late results cannot appear under the new context. A subsequent run uses the complete current request. |
| HNT-03 | Set Hunting identity to reader; perform a permitted write elsewhere; follow View evidence. | Navigation does not switch to a privileged identity or reveal preloaded evidence. Prior unrelated rows clear; authorized query execution remains required and the reader receives the real denial. |
| HNT-04 | When supported, run a query returning more than 200 rows and download JSON. If the backend caps returns at 200 or fewer, test that supported cap and record the contract evidence. | At most 200 rows display, with accurate returned/displayed counts and returned schema/types. Export contains the full returned result, not only the visible slice; server truncation is disclosed if reported. A lower verified server cap does not require expanding the query engine. |
| HNT-05 | Select available, unavailable, and restricted tables in the reference panel. | Actual catalog supplies table choices, availability, entitlement, native mapping notes, and column names/types. One example table is not presented as complete Microsoft coverage. |
| EVD-01 | Inspect historical fixture data, operation-produced evidence, and a changed fixture. | Labels reflect actual provenance and retain appropriate distinctions. Existence of a fixture or successful mutation alone is not proof of an operation evidence record. Unknown provenance is identified honestly. |
| CLK-01 | Use a synthetic evidence reference time far from current runtime; authenticate and query. | Evidence windows use the documented evidence reference; authentication/session/token validity uses runtime time. Both contexts are labelled correctly and never substituted for one another. |

## 7. Inspector, accessibility, and presentation

| ID | Role and procedure | Required outcome |
|---|---|---|
| INS-01 | Compare server configurations with Inspector disabled and enabled; attempt a direct route. | Disabled feature is unavailable even through a direct URL/request. Enabled Inspector exposes only curated, tenant-scoped read-only tables, selectable through the actual catalog. |
| INS-02 | Inspect allowed tables with more than 50 rows and probe excluded columns/actions. | 50-row cursor navigation works. Credentials, sessions, keys, unrestricted JSON, arbitrary SQL, and writes are inaccessible according to the server allowlist. |
| A11Y-01 | Complete primary flows using keyboard alone. | Labelled controls, skip link, visible focus, logical order, usable tabs, and named icon buttons work. Dialog focus is contained and returns to the opener; Escape/close behave consistently. |
| A11Y-02 | Trigger validation, denied access, asynchronous progress, success, and an empty list with assistive technology. | Status/error announcements are meaningful; errors are associated with inputs. Color is not the only indicator. Disabled actions have understandable prerequisites. |
| UI-01 | Type partial JSON/numbers/text, move the caret, blur/tab, then cause summary updates or polling. | Entered values, selection/caret, and focus survive updates. Blur must not rebuild live inputs or drop unsaved values. Background polling cannot reset unrelated forms. |
| UI-02 | Inspect every screen, dialog, and error state at desktop, tablet, and 320/375/390-pixel widths, plus zoom. | Columns/navigation collapse; all actions remain usable. Enabled Inspector/server tools and administrator/logout controls remain reachable through an accessible compact menu or equivalent. Tables/code scroll within their containers. Document body has no unintended horizontal overflow, including long IDs/errors. |
| UI-03 | Compare to handoff/reference at agreed desktop size and supplied themes. | Teal/lime palette, spacing, typography, panel/table hierarchy, states, and restrained surfaces match. Contrast and reduced-motion preferences remain usable; no missing external font/icon dependency breaks local operation. |

## Prototype review versus production completion

**Design-demo checks (D-01–D-04):** open the offline mockup; visit every page and dialog (D-01); exercise illustrative read/write denial, preview invalidation, and replacement (D-02); inspect composition, sample runs, and Hunting states (D-03); inspect narrow layouts and keyboard focus (D-04). These establish the intended presentation and selected interactions only. Sample login, local state, timers, placeholder upload, and JSON modals are expected prototype limitations, never acceptable production implementations.

**Production evidence (P-01–P-04):** provide the catalog/route/contract and optional-feature inventory (P-01); record relevant automated checks with exact commands/results and existing-regression status (P-02); demonstrate real backend success, denial, invalidation, isolation, recovery, and export flows mapped to IDs above (P-03); include representative desktop/mobile screenshots and unresolved blockers/deviations (P-04). At least one real server integration path must substantiate each implemented workflow; visual screenshots and mocked tests alone cannot pass P-03.

Completion requires all mandatory IDs to pass and each optional feature to have a supported implementation or evidenced N/A determination. Any unresolved required contract or failed regression remains visible in the final report.
