# MockAzure — Design system and implementation handoff

**Version:** 1.0 · **Prepared:** 21 October 2026  
**Product:** a local Microsoft cloud emulator console for developers, security engineers, and DFIR analysts  
**Companion files:** [interactive mockup](MockAzure-Interactive-Mockup.html), [implementation prompt](IMPLEMENTATION_PROMPT.md), [acceptance criteria](ACCEPTANCE_CRITERIA.md), [JSON tokens](design-tokens.json), [CSS tokens](design-tokens.css), [original brief](reference/ORIGINAL_BRIEF.md)

## 1. What this handoff defines

Build a usable console that helps a developer answer four questions: **What exists in this tenant? How do I populate useful connected data? Can this identity perform this operation? What evidence did that operation produce?** Resources is the landing screen. This design does not introduce an overview dashboard.

The HTML is a complete interactive **design reference** with sample data. It demonstrates layout, navigation, progressive disclosure, permission feedback, preview/apply interactions, profile composition, run reporting, and hunting. It is not a connected implementation. Sample records, counts, profile keys, run timers, permissions, URLs, and query results must not become production business logic.

No application repository was supplied for this handoff. The original brief describes the product and its existing constraints; it is not an audited inventory of the current branch. The implementing LLM must inspect the actual repository, tests, packaged frontend, and server contracts before wiring these screens.

### Authority and conflict resolution

1. Preserve the user's brief and its safeguards. Use the actual repository to establish existing contracts and capabilities.
2. Use this document for the intended visual system and interaction behavior. Where the repository cannot support a proposed behavior, identify the specific gap and implement the necessary support only within the authorized scope.
3. Use the interactive mockup for visual composition and examples. Use the acceptance criteria to verify the real implementation.
4. Never use mockup data to manufacture an apparent backend capability. If an actual contract conflicts with a brief requirement, expose the conflict and its resolution; do not quietly remove the requirement.

## 2. Product rules that shape the interface

### Administration and API access are separate

The console's shared administrator password authorizes lab administration. An emulated Microsoft API operation uses a **named test identity**, with permission checks enforced by the emulator. The interface must keep the selected operation mode, target tenant, identity, and relevant permissions visible next to the action.

Use the labels **Lab administration** and **Emulated API** consistently. Administrative fixture actions must never silently fall back from a denied API request. A console administrator can still see an API authorization error. The footer's explanation is a useful reminder, but the distinction must also be visible where decisions are made.

### Context is part of every operation

Tenant context belongs in the top bar and in every request, cache key, preview, and run. A visible tenant switch must clear or invalidate results that belong to the previous tenant. Late responses from an earlier tenant must not populate the current screen. Record identifiers and related-resource navigation must be resolved within the selected tenant.

### Writes are reviewed before application

The standard sequence is **edit → preview → review affected records → apply**. Reads can execute directly. A preview is bound to the exact operation inputs and context. Changing those inputs invalidates it. Replacement is a distinct operation with an explicit acknowledgment and a diff that includes removed properties.

### Evidence has provenance and its own reference clock

Distinguish generated fixtures from evidence produced by simulated operations. Creation provenance and the latest mutation are separate concepts; an API edit need not change the object's original fixture provenance. Show the evidence reference time wherever it affects interpretation. Authentication expiry and session timing must continue to use the actual authentication clock.

### Capabilities come from the emulator

Service and resource navigation must be driven by the actual catalog. Show friendly names first, with internal family identifiers and API paths available immediately beneath or in details. Unsupported operations need an explanation. Optional capabilities—related resources, activity batches, Hunting, and Inspector—depend on the actual server/branch. The seven service labels in the brief do not imply full Microsoft API coverage.

## 3. Information architecture and screen contract

| Navigation label | Page heading | Primary user job | Main content |
|---|---|---|---|
| Explore resources | Explore your cloud. | Find and inspect persisted objects | Service selector, family navigation, filters, paginated table, details |
| API workbench | Test the interaction. | Read or change objects in an explicit access context | Mode, target, identity, operation, payload, preview, response |
| Compose lab | Build a useful starting point. | Assemble and populate a reusable dataset | Ordered layers, tenant target, parameters, population preview |
| Population runs | Follow every step. | Monitor and recover population work | Run detail, counters, technical steps, history, exports |
| Hunt evidence | Follow the evidence. | Query supported evidence through a test identity | Cookbook, KQL, timespan, result table, table reference |
| Inspector | Look under the surface. | Inspect allowed persisted tables | Server-enabled table catalog and read-only pagination |
| Console admin | Enter your lab. | Authenticate to lab management | Shared password and first-time setup explanation |

The prototype's **Design notes** entry is a review aid. Omit it from the production navigation or relocate it to development documentation. Preserve every existing user workflow even when the prototype represents it only with an illustrative dialog.

### Routing and context restoration

Introduce URL-addressable UI state if the actual frontend still uses in-memory navigation. Reuse an existing router if present. A small hash router is a suitable default for a Python-served static frontend because it can avoid requiring new server path fallbacks. A proposed shape is `#/resources?tenant=<id>&family=<key>`; this is a **UI route proposal**, not a backend endpoint.

Represent view, tenant, family, and inspected record in the URL where appropriate. Support back/forward navigation. Validate deep-linked targets through the server. Do not place passwords, tokens, payload JSON, or private result data in the URL. Run identity includes its tenant; opening a run from history must not relabel it with the current composer's target.

## 4. Visual foundation

The visual direction is a compact technical workspace: forest teal navigation, a warm pale workspace, lime selection and action accents, clear borders, small corner radii, and very little elevation. Tables and forms carry the information. Avoid decorative metrics, gradient hero sections, oversized empty cards, and unrelated charts in working screens.

### Color tokens

The following values match the reviewed prototype. The downloadable JSON and CSS contain the same palette, fixed brand colors, dimensions, and proposed production interaction tokens.

| CSS variable | Purpose | Light | Dark |
|---|---|---|---|
| `--ma-paper` | Workspace background | `#F5F6F1` | `#152325` |
| `--ma-panel` | Panels, inputs, top bar | `#FFFFFF` | `#1D2D2E` |
| `--ma-ink` | Primary text | `#163C3E` | `#E8EEE7` |
| `--ma-muted` | Secondary text | `#627574` | `#A6B9B6` |
| `--ma-line` | Dividers and quiet borders | `#DCE3DC` | `#374A48` |
| `--ma-soft` | Secondary surfaces | `#EEF2EB` | `#263A37` |
| `--ma-good` / `--ma-good-bg` | Success and verified state | `#23674D` / `#EAF5EC` | `#A7E0BC` / `#254334` |
| `--ma-amber` / `--ma-amber-bg` | Review, warning, needs attention | `#8C5714` / `#FFF4DC` | `#EFD49B` / `#443A26` |
| `--ma-red` / `--ma-red-bg` | Error and denied state | `#A44242` / `#FBEDEB` | `#F0B0AD` / `#432F30` |
| `--ma-blue` / `--ma-blue-bg` | Informational state and technical emphasis | `#355C99` / `#EAF0FA` | `#B1CEF4` / `#2B3B50` |

Fixed colors across themes: `--ma-forest: #153B3D`, `--ma-lime: #D1F571`, and `--ma-nav: #B8CFCA`. Sidebar main text is `#EDF4E9`; inactive navigation is `#D4E2DB`; active navigation text is `#173C32`. A forest primary button uses `#F4F7ED` text; a lime button uses `#143B32` text.

Use lime selectively for the current navigation destination and a key forward action. Use semantic colors with a written label and, when useful, an icon. A green dot alone must never be the only representation of a successful run. The palette is a visual reference, not a claim that every possible foreground/background pairing meets an accessibility standard; verify the actual pairs at their final sizes.

### Typography

| Role | Reference size | Weight / line height | Implementation guidance |
|---|---|---|---|
| Page heading | 29px desktop, 25px compact | 600 / 1.2 | Letter spacing −1.05px; keep the headline short |
| Section heading | 17px | 600 / approximately 1.5 | Letter spacing −0.25px |
| Component heading | 14px | 600 / 1.5 | Use for panel subsections and meaningful groups |
| Body | 14px | 400 / 1.5 | Default readable explanation and message size |
| Dense controls and tables | Prototype varies from 11–12px | 400–500 / 1.5 | Target at least 12px in production |
| Secondary metadata | Prototype includes 9–10px | 400–500 / 1.4–1.7 | Target at least 11px; do not preserve tiny reference text literally |
| JSON, paths, identifiers | 12px | 400 / 1.5; editor 1.9 | Preserve selection, wrapping or contained horizontal scroll |
| Login statement | 46px desktop, 35px compact | 500 / 1.1 | A one-time entry screen, not a recurring workspace hero |

Use `Segoe UI, -apple-system, BlinkMacSystemFont, Arial, sans-serif`. Use `Consolas, SFMono-Regular, Menlo, monospace` for code. No remote font download is required. Preserve density by prioritizing content and disclosure, rather than shrinking text until it is difficult to read. Compact-screen text inputs use 16px in the reference.

### Spacing, shape, and layout dimensions

Use a base spacing scale of **4, 8, 12, 16, 24, 32, 40, 48px**. Optical values in the prototype—such as 18px card padding and 17px column gaps—are deliberate reference measurements, not a mandate to scatter unrelated literals through the implementation.

| Element | Reference dimension |
|---|---|
| Sidebar | 202px at ≥1180px; 184px default; 154px at ≤850px |
| Top bar | Minimum 68px; 14px × 26px padding |
| Main workspace | 32px wide desktop; 28px × 26px default; 22px × 18px tablet; 22px × 15px mobile |
| Resource family rail | 155px wide desktop; 138px default; 110px tablet |
| Two-column forms | `minmax(0, 1.65fr) minmax(190px, 1fr)`; 17px gap |
| Hunting reference rail | 206px, then stacked at compact widths |
| Panel | 1px border, 7px radius; 18px content padding |
| Buttons / inputs | 5px radius; reference button minimum height 34px |
| Navigation item | 6px radius; reference desktop minimum height 40px |
| Badge | 4px radius; 3px × 7px padding; production text ≥11px |
| Record drawer / wide dialog | 430px / 620px maximum preferred width; 9px radius |
| Table cells | 11px × 14px default; 14px vertical padding on wide desktop |
| Outer reference frame | 12px radius; production application may run edge to edge |

Panels use borders instead of shadows. Reserve elevation for dialogs: reference shadow `0 12px 60px #071B2433` and scrim `#0C25244D`. In production, modal positioning and scrolling must work relative to the viewport, including long pages; do not inherit the prototype's absolute-positioning shortcut without review.

### Icons, motion, and focus

Use a consistent outline icon set. The offline reference embeds **Lucide 0.468.0** with its license notices. Typical icons are 16px and accompany text labels. Icon-only controls need accessible names and a visible tooltip or equivalent explanation when useful.

Use immediate state changes for data and permission feedback. Small opacity or color transitions are sufficient; honor reduced-motion preferences. Provide a visible focus indicator on every interactive control. The token files propose a 2px theme-aware focus outline with a 3px offset; verify it against each component surface. Focus visibility, text sizes, and larger touch targets are implementation requirements beyond merely reproducing a screenshot.

## 5. Component contracts

### Application shell and tenant selector

The dark sidebar anchors the product identity and task navigation. The top bar contains a short breadcrumb, the selected tenant, and refresh. Show a human-readable tenant name with its slug or identifier available nearby. Disabled/loading tenant selectors must explain why they cannot change. Refresh clears stale previews and reloads the actual data. It must not signal success before the request completes.

On a tenant change, close or re-resolve record details, clear query outputs and API responses, reset tenant-specific cursors, and invalidate operation/population previews. Preserve reusable composition drafts only if their target is clearly updated and they cannot be applied without a new preview.

### Resource explorer

Service selection narrows family navigation. A family entry uses a friendly label and a count only when that count is known. The header shows the family identifier and relevant API path, with **Supported operations & limits** available without hunting through documentation.

The toolbar supports resource search, scope/parent, JSON filter, and Load. Preserve the existing scope and JSON filter capability even if a friendly filter is added. Clearly distinguish filtering the loaded page from server-side search. Do not claim a search covers the whole tenant if only the current page is available.

Use catalog-selected columns, up to six initially. Keep name and ID discoverable; long IDs can wrap or be copied without truncating the actual value. Preserve 50-record cursor pagination with First and Next. Do not invent page numbers or totals when the API provides only cursors. Interactive sorting or configurable columns may be added only with honest local/server semantics.

### Record detail drawer

The drawer has a title, friendly type, stable ID, provenance, and a close control. Tabs expose **Overview**, **Related resources**, and **Raw JSON**. The overview is a concise scalar summary appropriate to the known schema; raw JSON remains available. Related records use real relationship data and tenant-scoped navigation. Hide or explain this tab when the server cannot supply relationships.

Provide **Prepare an edit**, supported content downloads, and copyable identifiers. Preparing an edit carries the exact family, scope, target ID, tenant, and mode to the workbench. It does not apply a change. If the resource has no supported mutation, show the reason. Preserve the calling element and return focus on close.

### Operation workbench and identity card

Place the request editor beside a compact identity/context card on desktop, stacking the card on compact screens. The mode selector must remain visible. Show family/operation, target scope or record, named identity, applicable permission information, payload, and supported limits. Identity details are informational; only the server can authorize execution.

Expose automatically generated scalar fields where schemas support them and keep advanced JSON accessible. Synchronize both representations without discarding unknown supported fields. Malformed JSON gets a local, specific validation message. Unsupported fields and authorization failures must still be validated by the server.

Use **Run request** for reads and **Preview change** for writes. Display actual status, response body or no-content result, and useful server metadata. The prototype's user GET/PATCH examples do not limit production to users or those two methods; expose every operation supported by the actual catalog.

### Preview diff and apply controls

A preview names the mode, tenant, identity when applicable, operation, affected IDs, scope, change count, warnings, and before/after differences. Represent added, changed, and removed fields distinctly. Do not style absent values as an empty string when the operation actually removes a property.

Apply is disabled until a current preview exists. If replacement is intended, show **Allow this replacement** as a separate explicit acknowledgment. A stale preview stays visibly stale or is removed; it must never look applicable. Clear the acknowledgment when the target or replacement proposal changes. After success, clear the preview, report the real result, and refresh affected resources. Do not show success from a timer or a client-side mutation alone.

Selecting replacement as the operation changes the proposal and requires a new preview. Checking the acknowledgment for that exact preview does **not** change the proposal or invalidate it; it satisfies the additional Apply prerequisite. Unchecking acknowledgment disables Apply. Keep these two concepts separate in state and tests.

### Upload content

Use a file input, file name and size, progress, failure/retry state, and a returned content ID. Reject browser uploads over **16 MiB** before submission, and retain server-side validation. Apply the actual server's supported content constraints. A selected file is not an uploaded file. Copy or insert the returned content ID only after successful completion.

### Profile layers and composition steps

Group the composition into **Choose layers → Configure → Review**. These are visual steps over one draft, not isolated forms that lose input on navigation. Each ordered layer has a friendly label, technical key on demand, remove, move up, and move down controls. Keyboard controls must provide the same functionality as pointer actions.

Keep the summary visible beside the draft on desktop. Show tenant profile, ordered data layers, target tenant, namespace, scale, seed, and optional activity. Counts remain estimates until the actual population preview returns. Moving a layer or changing any parameter invalidates that preview.

The advanced source section preserves JSON/YAML import, direct JSON editing, export, saved bundles, and revision handling from the actual application. A parser or server validation failure must preserve the user's source for correction. Do not replace revisions with browser-only storage in production.

### Runs and recovery

The run detail appears above history. Lead with status and progress, then Created, Unchanged, and Verified counters, errors, technical steps, and exports. Display both durable run ID and tenant identity. Keep each run's original configuration immutable even if the composition form changes later.

Support pending, running, verifying, succeeded, failed, and needs-attention states according to server truth. Poll active runs with bounded backoff and stop appropriately. Navigation among history items must not stop a background run or mix counters between runs. Cancel requests cancellation; explain whether the server has acknowledged it. Cancellation does not imply rollback of records already created. Resume/recheck must follow the server's recovery model, including any idempotency behavior it actually guarantees.

Manifest and ground-truth actions download real files. If finite activity batches are supported, show action progress, pacing, and evidence reference time separately from fixture creation. Do not turn the finite batch into an unbounded background workload.

### Hunting and table reference

Keep the named test identity adjacent to the query controls. Show relevant permission guidance and entitlement/capability information. Preserve cookbook examples, plain multiline KQL, timespan, Run query, and Download JSON. A richer editor is optional and must not imply query-language support absent from the emulator.

The result header names returned rows, displayed rows, and evidence reference time. Display at most **200 rows** in the browser. Export the **complete returned result**, including schema/type information where provided; this is not a promise to export every underlying record beyond the query's actual returned result. After query, timespan, tenant, or identity changes, mark prior results stale and disable export until a fresh successful query completes.

The reference rail exposes table availability, entitlement, native evidence mappings, and column names/types. Query failures need useful errors without fabricated results. The prototype's two cookbook examples are illustrative only; production delegates queries to the real emulator's supported engine.

### Optional Inspector and login

Inspector appears only when enabled by the server and exposes only curated, tenant-scoped, read-only tables. Preserve 50-row pagination. Exclude credentials, sessions, keys, and unrestricted JSON columns through server enforcement. UI hiding alone is insufficient.

The login screen uses two columns: dark product statement on the left and a shared-password form on the right. At narrow widths they stack. Include sign-in progress and an inline authentication error. The expandable setup help must use the actual repository's CLI command; the brief does not specify its exact syntax. Do not add registration, SSO, or personal-account management as part of this redesign.

## 6. State and interaction rules

### Write state model

| State | What the user sees | Allowed next action |
|---|---|---|
| Draft | Editable operation inputs | Validate and request preview |
| Previewing | Request in progress; no applicable previous preview | Wait or edit/cancel according to request handling |
| Ready for review | Server-derived effect, IDs, warnings, and context | Apply current preview; acknowledge replacement if required |
| Stale | Inputs or context no longer match | Request a new preview |
| Applying | Exact reviewed proposal in flight | Wait; prevent duplicate submission |
| Succeeded | Actual server outcome and useful follow-up | Inspect updated data or compose another operation |
| Failed / conflict / expired | Specific failure with preserved draft | Correct inputs, re-authenticate, or re-preview as required |

The following invalidate a write preview: tenant, mode, identity, resource family, operation, target ID, scope, payload, content ID, and every other input the actual server uses to define the change. Population preview invalidation additionally includes tenant target, new-tenant fields, tenant profile, ordered layers, namespace, all parameters, seed, source import, activity recipe, count, and pacing. Include replacement authorization in the application contract as the real server requires; changing the proposed replacement must reset acknowledgment.

Use the existing preview/apply contract. If it supports an opaque preview token or version, bind it to the exact request and send that token on apply. If it does not, inspect how the server guarantees that the applied operation matches what was reviewed and address any gap. A browser-only fingerprint is a useful stale-state check, not a security boundary.

### Common states and microcopy

| State | Required behavior | Example copy |
|---|---|---|
| Initial loading | Keep context visible; avoid showing a false zero count | “Loading users for Contoso Lab…” |
| Empty tenant or family | State the scope and offer a relevant next step | “No users in this tenant. Compose a lab or add a supported fixture.” |
| No filter results | Preserve inputs and offer reset | “No records match this filter.” |
| Invalid input | Label the field and preserve its value | “Enter a JSON object for the filter.” |
| Permission denied | Name the identity and denied operation; use server details | “Directory reader cannot update this user.” |
| Unsupported capability | Explain the supported boundary | “This resource does not support updates in this emulator.” |
| Stale preview | Disable apply; explain the cause | “The payload changed. Preview again before applying.” |
| Network failure | Preserve drafts; avoid implied completion | “The request could not be confirmed. Refresh the operation status before retrying.” |
| Success | Name what actually completed | “User updated. Inspect the record or view resulting evidence.” |

Treat uncertain write outcomes differently from confirmed failures. Do not automatically retry a mutation without understanding the server's idempotency or reconciliation contract. If only selected fields are invalid, keep the rest of the user's work.

## 7. Responsive and accessible behavior

At **1180px and above**, use the wider sidebar and resource rail. Between **851px and 1179px**, use the default compact desktop layout. At **850px and below**, stack workbench/composer/reference columns and reduce main padding. At **650px and below**, turn primary navigation and resource families into wrapping horizontal controls, stack login columns, and allow the top bar and action rows to wrap.

Keep wide tables within their own horizontal scrollers; the reference uses a 520px minimum table width on mobile. The page itself must not overflow horizontally at 320px. Do not hide essential identity, tenant, permission, error, or action context on mobile. If a provenance column is omitted at compact widths, retain it in details or another accessible location.

The frozen prototype hides its lower sidebar on narrow screens, including Inspector and Console admin. **Fix this in production:** provide a compact accessible menu or another visible route to server tools and administrator/logout controls. Inspector remains server-gated; session controls remain reachable at all widths. The offline reference can show Inspector and Login at narrow widths after opening them at desktop width.

Production controls should target at least **44px** for coarse-pointer interaction. This is a design requirement for the implementation; the compact mockup contains smaller reference controls. Preserve desktop density while enlarging mobile hit areas. Use logical document order, visible focus, an early skip link, real labels, appropriate button/link semantics, and polite status announcements. Reserve assertive announcements for errors requiring immediate attention.

Dialogs need a name, modal semantics, focus containment, Escape dismissal when safe, and focus return. Background content must not remain interactable while a modal is active. Keyboard users must be able to select services/families, inspect rows, edit payloads, reorder layers, review a diff, apply a valid operation, and run/export a query. Do not hijack ordinary text selection or editing shortcuts. If using tabs, implement the corresponding keyboard and selected-state behavior; otherwise use honest navigation buttons.

Render record values and server errors as text, not executable markup. Handle long identifiers, empty values, large JSON, and unexpected Unicode without breaking layout. Respect reduced motion and system light/dark preference. If an explicit theme setting already exists, preserve it. The token CSS supports `data-ma-theme="light"` or `"dark"` as an optional integration hook; it does not require a new theme-control feature.

## 8. Engineering handoff

### Preserve the packaging model

The brief describes packaged HTML, CSS, and vanilla JavaScript modules served by Python. Retain that deployment model unless repository inspection establishes another existing approach. Organize code into small view, component, state, and API-adapter modules using the project's conventions. Do not introduce a separate build system or a framework migration merely to reproduce these screens.

The original fragment demonstrates behavior through shared in-memory state and broad HTML rerenders. It is useful reference code, but production should preserve focused inputs, cursor position, composition events, and in-flight requests. Prefer scoped updates and stable controls. Keep tenant, request, run, and preview state explicit rather than distributed through incidental DOM values.

### Discover before connecting

Build a short capability matrix from the repository: authentication, tenant catalog, resource families/operations, read/filter/page contracts, preview/apply, upload/content download, profile catalogs/revisions, population preview/run, cancel/resume/export, Hunting, relationships, and Inspector. Record actual files, methods, endpoints, schemas, and limitations. This handoff deliberately supplies no invented REST endpoint map.

| Design capability | Expected implementation category | Required evidence |
|---|---|---|
| Palette, shell, navigation, forms, statuses | Presentation | Rendered views and interaction checks |
| Friendly catalog labels, detail summaries, progressive disclosure | Frontend using available metadata | Actual catalog/schema fields and safe fallbacks |
| Deep links and back/forward | Frontend route enhancement | Validated URL state and tenant-scoped lookups |
| Identity authorization, preview binding, tenant isolation | Server-enforced behavior | Existing tests or added integration checks |
| Related resources | Optional newer-branch capability; gate when absent | Real relationship contract; no inference from names |
| Activity batches and evidence clock | Optional newer-branch capability; gate when absent | Finite recipe execution and reported clock metadata |
| Hunting and reference metadata | Optional newer-branch capability; gate when absent | Supported query, entitlement, and schema contracts |
| Inspector | Server-enabled existing optional feature | Curated read-only allowlist and denied sensitive data |

A missing **required** contract is a concrete integration gap, not an excuse to populate a successful-looking panel with hard-coded output. Missing **optional/newer-branch** features are gated out and reported as evidenced N/A unless backend expansion is separately authorized. Preserve all supported workflows and report genuine gaps. Add backend support only after establishing its intended semantics and scope; do not silently widen the emulator into an unrestricted Microsoft cloud implementation.

### Data integrity and request handling

Cache by tenant and operation context. Abort obsolete reads where possible and reject late responses by context/version even when abort is unavailable. Use actual cursors, IDs, result counts, error bodies, run states, and export streams. Keep production sample/demo mode isolated from connected operation, if a demo mode is retained at all. Never send a request to real Microsoft services merely because a mockup displays a Microsoft-style API path.

Keep authentication and session handling in the established server mechanism. Do not embed shared passwords or real API credentials in HTML, URL state, logs, or exported bundles. Keep review/apply controls resilient to double clicks and authentication expiry. Follow the existing application's request-protection conventions rather than substituting client-only checks.

## 9. Reference limitations and production deltas

| Interactive mockup behavior | Required production behavior |
|---|---|
| Small sample arrays and local filtering | Real catalog-driven resources, server loading, honest search scope, cursor pagination |
| Example user GET/PATCH and fixture replacement | All actually supported operations across enabled services |
| Client-side illustrative permission checks | Authoritative emulator enforcement for every operation |
| Local fingerprints and diffs | Server-derived preview effects and application of exactly reviewed inputs |
| New tenant duplicates sample data | Real tenant creation and population using isolated persistent storage |
| Population counts are sample arithmetic | Actual population preview counts, warnings, bounded samples, and digest |
| Timed run animation | Durable server-backed status, cancellation, recovery, and exports |
| Two recognized cookbook queries | Actual supported query execution, errors, schemas, and returned-result downloads |
| File selection shows a size message | Real upload progress, validation, and returned content ID |
| Export buttons open JSON review dialogs | Actual downloadable bundle, manifest, ground truth, and query result files |
| Saved revisions last only in the preview session | Real supported revision persistence and loading |
| Sign-in only enters the demo | Real shared-password authentication and session behavior |
| State resets when the HTML reloads | Persistent server data and durable runs; intentional local draft behavior |
| Prototype navigation and modals | Deep links, reachable mobile server/session controls, robust focus behavior, viewport-safe dialogs, production-sized text/targets |

The offline mockup intentionally makes no network requests and cannot become connected simply by changing a base URL. Its embedded source exists to make visual and behavioral details inspectable. Implement the actual adapters and server-backed workflows.

## 10. Review and completion

Use [ACCEPTANCE_CRITERIA.md](ACCEPTANCE_CRITERIA.md) as the verification checklist. The implementing LLM should deliver changed source, a contract/capability map, meaningful test evidence, and a concise summary of remaining genuine limitations. It should not stop at a plan, screenshots, or a frontend that only appears functional.

Review resources, record details, API reads/writes and denied actions, replacement, upload, composition, population review, active/failed/recoverable runs, hunting, optional Inspector, and login in both light and dark appearances. Include desktop, tablet, 390px, and 320px widths, keyboard use, stale asynchronous responses, tenant changes, and preview invalidation. A successful screenshot does not verify authorization, persistence, run recovery, or data isolation; those need real integration checks.

## References

- [Original user-provided product brief](reference/ORIGINAL_BRIEF.md): source for product purpose, existing deployment, workflow constraints, limits, and newer-branch distinctions.
- [Reviewed source fragment](source/MockAzure-Source-Fragment.html): source for reference visual values and sample interactions. The standalone export embeds this fragment unchanged.
- [Microsoft Graph: update user](https://learn.microsoft.com/en-us/graph/api/user-update?view=graph-rest-1.0): external reference for the illustrated user update API. Check the permissions and fields for the exact operation being implemented; sample identity permissions are not a universal or least-privilege prescription.
- [Microsoft Graph: runHuntingQuery](https://learn.microsoft.com/en-us/graph/api/security-security-runhuntingquery?view=graph-rest-1.0): external reference for the illustrated Hunting API. Microsoft's documentation does not establish the emulator's actual supported subset.
- [Third-party notices](licenses/): embedded icon licensing for the downloadable reference.
