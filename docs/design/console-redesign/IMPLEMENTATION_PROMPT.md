# MockAzure — implementation prompt

You are the coding agent responsible for fully implementing the supplied MockAzure console redesign in the actual application repository. Build the working application, wire its workflows to real supported server behavior, verify it, and report the result. Continue through implementation and verification; a plan, static reproduction, or partial user-edit demo is not completion.

## 1. Read the handoff and inspect the repository

Read these supplied files before making changes:

- `DESIGN_SYSTEM_HANDOFF.md`: visual system, screen specifications, interaction rules, and implementation boundaries.
- `ACCEPTANCE_CRITERIA.md`: concrete completion requirements and regression cases.
- `MockAzure-Interactive-Mockup.html`: runnable visual and interaction reference.
- `reference/ORIGINAL_BRIEF.md`: original product requirements and operating constraints.
- `source/MockAzure-Source-Fragment.html`: original interactive fragment retained for reference.
- `design-tokens.json` and `design-tokens.css`: reusable visual values and clearly identified production readability/interaction targets.

Inspect repository instructions such as `AGENTS.md`, application entry points, static assets, route handlers, authentication/session middleware, tenant scoping, capability/resource catalogs, operation schemas, preview/apply contracts, profile storage, run persistence, query endpoints, and existing tests. Run the relevant baseline checks. Record actual routes, payloads, response shapes, permissions, pagination, error semantics, and feature flags before implementing their adapters. Do not infer a server contract from the prototype.

The described application uses packaged HTML, CSS, and vanilla JavaScript modules served by Python, without a separate frontend build system. Preserve its deployment, packaging, framework, and dependency model. If the actual branch already uses a different stack, follow that established stack; do not migrate it merely to match either this sentence or your preferred tools. Preserve local operation and package required assets locally.

Brief and verified server semantics govern behavior; the design handoff and mockup govern presentation. Preserve an existing workflow even when the focused prototype does not implement it. Identify mismatches explicitly. Implement a verified adapter or narrowly scoped server integration where existing capabilities support it. If a required workflow lacks a genuine server contract that cannot safely be completed within the existing model, report that workflow as blocked with the exact missing contract. Do not silently omit it, create fabricated success, or declare the application complete.

## 2. Scope and product structure

MockAzure is a local Microsoft cloud emulator for developers, security engineers, and DFIR analysts. Implement the design across every resource family and operation supported by its real catalog: Entra, Microsoft Graph, Exchange, Azure resources, Storage, Key Vault, Logs, and any other exposed families. The user GET/PATCH example illustrates the workbench; it does not limit implementation scope or establish universal field or permission rules.

Use Resources as the landing page, with no invented overview dashboard. Implement the shell and login; Explore resources and record details; API workbench and administrative fixture operations; Compose lab; Population runs; and the branch-supported Hunting and Related resources features. Implement finite activity controls when supported. Inspector exists only when enabled by the server. Feature detection must come from verified application configuration or capabilities, not an assumption based on mockup visibility. Explain absent newer-branch features in the completion report; do not represent them as active functionality.

Match the dark teal navigation, lime active state, warm workspace, restrained panels, system typography, monospace technical values, information hierarchy, spacing, and responsive behavior in the handoff. Use semantic tokens and reusable controls. Provide actual loading, empty, validation, authorization, unavailable, conflict, success, and recovery states. Keep identifiers, JSON, API paths, and technical limits accessible without making them prerequisites for ordinary navigation.

## 3. Identity, authentication, tenancy, and navigation

Use the real shared administrator-password login and session lifecycle, with the repository's actual first-time CLI setup command. Provide sign-in failure, pending, expiry, and logout behavior. Do not add registration or individual console accounts. Never accept arbitrary passwords as the mockup does, expose credentials, or store the shared password in client persistence.

Keep lab administration distinct from emulated API access. Browsing stored fixtures and composing populations uses management authorization. Public API operations and Hunting use the chosen named test identity and server-enforced permissions. Console sign-in must never grant an identity extra API permissions. Never retry a denied action under the administrator, switch identities silently, or trust a frontend permission badge as authorization. Derive permission guidance from verified metadata; verify current Microsoft documentation for the actual selected operations, then distinguish Microsoft's contract from the emulator's implemented subset. Do not invent arbitrary Graph, REST, KQL, or secondary-effect coverage.

Keep the active tenant selector and refresh action visible, and send the verified tenant identifier on every relevant request. Key caches, record selections, previews, query results, uploads, and run detail by tenant and applicable identity. Clear or isolate stale state when context changes, cancel obsolete requests where possible, and ignore late responses from previous contexts. Related-resource links must use real relationship IDs or mappings supplied by the server, never department, display-name similarity, or other guessed associations.

Provide addressable routes for screens, tenant-scoped resource families and records, and selected runs using the existing server's navigation conventions. Support direct load, refresh, browser Back/Forward, invalid IDs, and expired sessions. Keep secrets, preview tokens, credentials, and request bodies out of URLs. Navigation must not reapply mutations or retarget a request implicitly.

## 4. Resources and operation workbench

Build service and family navigation from the capability catalog, with friendly labels and retained technical identifiers. Load persisted records using the real scope/parent and JSON-filter contracts. Display up to six catalog-selected columns with prominent identifiers; preserve inspect, content downloads, and applicable actions. Implement 50-record cursor pagination with First and Next controls. Label text search and simple client filters as applying to the loaded page; do not imply global search, backend sorting, totals, or filtering the server does not supply.

Record details combine useful catalog-backed summaries, provenance, related resources when available, and complete formatted JSON. Provide Prepare an edit and real supported downloads. Carry the verified tenant, family, target, scope, and operation into the workbench. Implement Add, Edit, and every supported catalog operation through a reusable schema-driven form, retaining automatically generated scalar fields and advanced JSON. The prototype's Add shortcut is not a finished Add workflow.

The workbench must clearly identify Emulated API versus Admin fixture mode, named identity where required, service, operation, target, scope, parameters, payload, and limitations. Read-only requests may execute directly where the contract permits. Mutations follow payload → server preview → effect review → apply. Display actual affected IDs, field changes, warnings, replacement/removal consequences, and only server-reported secondary effects. A MockAzure preview is not a Microsoft Graph dry-run endpoint.

Inspect and use the actual authoritative preview/apply mechanism. Reuse preview tokens/IDs, digests, versions, expiry, and concurrency checks where the verified contract supplies them; do not assume those fields exist. Bind the preview to all operation-defining inputs: tenant, authorization context and identity, mode, family, operation, target, scope/parent, scalar fields, JSON payload, parameters, uploaded content IDs/files, and selection of replacement as the operation. Any such change immediately invalidates Apply and previous response context. Ignore previews that finish after inputs change. A client fingerprint alone is insufficient to guarantee application of the reviewed proposal. On apply, the server must revalidate authorization and the reviewed context using its actual contract. Identify missing binding or conflict protections as explicit backend work, with documented semantics, rather than inventing endpoints or claiming existing token guarantees. Explain conflicts and require a new preview; prevent duplicate submissions using the server's supported semantics.

Replacement requires an explicit checkbox acknowledging that exact preview. Selecting replacement as the operation changes the proposal and requires preview; checking its acknowledgment afterward enables Apply without invalidating that same proposal. Editing bound inputs clears acknowledgment; unchecking disables Apply. Keep operation intent and consent as separate state. Replacement must replace the record according to the server contract, including omission/removal behavior, rather than merge a patch while claiming replacement. Switching to a fixture shortcut must clear previous API response, preview, consent, and implied identity result.

Implement real Upload content: file selection, size validation, progress, errors, and the returned content ID for supported operations. The browser maximum is **16 MiB = 16,777,216 bytes**. Enforce the server's constraints too. Changing files or content references invalidates dependent previews. Do not substitute a file-name check or fabricated ID for an upload.

## 5. Profile composition and durable population

Implement the guided Compose → Configure → Review flow while retaining the full model: saved bundles and loading; templates; selectable tenant profile; ordered data profiles with add/remove/up/down; existing versus new tenant; new slug/domain/display name; dataset namespace; user count; activity days; random seed; and additional JSON parameters. Use real catalogs and validation ranges, not the sample profile keys or numeric limits unless confirmed. Preserve supported layer-order and merge semantics.

Retain JSON/YAML import, real bundle export, direct source editing, and persistent saved revisions through existing contracts. Preserve all supported composition fields during round trips; report invalid or unsupported input rather than dropping it. Saving a revision must actually persist it; displaying JSON in a modal is not an export. Reconcile scalar and source editing without losing values.

Population preview must resolve the exact composition on the server and show counts, warnings, bounded samples, affected resources, digest, and replacement consequences when present. Any change to tenant/profile/layer order/destination/new-tenant fields/namespace/counts/days/seed/parameters/source/activity settings invalidates it. Apply only that preview and transition to the actual returned run ID. Never derive final counts from the prototype's formulas or clone its sample tenant to simulate creation.

When the branch supports finite activity, expose recipe step, bounded action count, and pacing in milliseconds, validated against real supported recipes. Keep activity verification and operation-produced evidence distinct from fixture population. Do not introduce an unbounded traffic generator or imply broader operation coverage.

Implement Runs from persisted server records, including pending, running, verifying, succeeded, failed, and needs-attention when present in the real state model. Render progress, completed/total steps, Created/Unchanged/Verified counters, technical steps, errors, and real manifest/ground-truth downloads. Present the actual immutable resolved configuration for each run; keep draft composition and selected history separate from active-run state.

Poll active runs with bounded scheduling and cleanup; navigation, reload, disconnection, and application restart must recover from the server. Inspecting history must not stop an active run, rewrite its configuration, or redirect its polling responses into another run. Address every command by tenant and run ID. Cancel reports a request pending until server acknowledgement, handles completion races, and states that completed work may remain. Never promise rollback. Resume and recheck use the real recovery contract and persisted progress, not a restarted browser timer or invented idempotency guarantee.

## 6. Hunting, evidence, and Inspector

If supported, implement the named-identity Hunting workbench with cookbook selection, editable multiline KQL, timespan, real query execution, and JSON download. Preserve existing syntax/limits and show actual parser, authorization, entitlement, and availability errors. Custom KQL must reach the supported backend evaluator or return an honest unsupported-query error; it must never receive canned cookbook results. Do not build a pretend general KQL engine.

Show returned and displayed row counts, evidence reference timestamp, returned column names/types, and a maximum of 200 displayed rows. Export the complete returned result, including its schema/metadata as supported, not merely the displayed slice. Do not call it all matching data if the backend returned a bounded result. Refresh invalidation must cover tenant, identity, query, cookbook, timespan, and other request parameters; stale or unauthorized results must not remain exportable.

The table-reference panel uses the actual table catalog, selectable tables, availability, entitlement, native-evidence mappings, and column schemas. Preserve reported distinctions among unavailable, unsupported, and permission-denied states. Keep the evidence reference clock separate from runtime authentication time; never use synthetic evidence time to validate sessions or tokens.

View evidence is a contextual navigation action, not an authorization bypass. Preserve the chosen Hunting identity, clear unrelated results, and require an authorized real query. A directory reader must not see evidence merely because the preceding edit succeeded. Label generated fixtures, historical fixture records, and evidence produced by simulated operations truthfully using actual provenance. An API success does not prove an evidence row exists until the server reports it.

Inspector must remain optional, tenant-scoped, curated, and read-only. Implement its server-approved table selector, columns, and 50-row pagination. Exclude credentials, sessions, keys, and unrestricted JSON columns according to the server allowlist. Reject direct access when disabled; never expose a general SQL or database administration interface.

## 7. Implementation quality and completion evidence

Use maintainable modules and the repository's established patterns. Separate API adapters, context/routing state, view components, forms, and polling. Do not transplant the prototype's monolithic `innerHTML` renderer, in-memory sample permissions, synthetic timestamps, canned datasets, counts, or timers as application architecture. Escape untrusted record/error content and use the existing request/security conventions.

Preserve labelled controls, skip link, visible focus, meaningful headings, keyboard operation, and live status/error announcements. Implement dialog focus containment and return, usable tabs, disabled-action explanations, and accessible error associations. Update summaries without replacing live inputs: typing, blur, tabbing, and asynchronous refresh must preserve current values, caret, and focus. Collapse columns/navigation at narrow widths; scroll oversized tables or code inside their containers. Fix the reference's hidden lower sidebar: provide an accessible compact menu or visible routes for enabled Inspector/server tools and administrator/logout controls. The document body must not overflow horizontally at 320px, and session controls must remain reachable. Respect reduced motion and retain usable zoom and contrast.

Use `ACCEPTANCE_CRITERIA.md` to drive focused unit/integration/browser checks. Exercise real local backend flows with isolated fixtures, two tenants, permitted and denied identities, stale previews, replacement, cancellation races, recovery, pagination, upload limits, and query export. Verify all screens visually at desktop and narrow widths and both supplied themes when implemented. Prototype demonstrations and mocked unit tests are useful but do not establish production integration.

Deliver code plus a concise implementation report: changed components and routes; verified capability/contract mapping; tests and exact results; screenshots or other meaningful UI evidence; optional features detected; deviations with reasons; and genuine blockers with the missing server contract and user impact. State which acceptance IDs passed, failed, are blocked, or are inapplicable with evidence. Do not label the whole redesign complete while a required workflow remains a placeholder or an unresolved failure.
