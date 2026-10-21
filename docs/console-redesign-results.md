# Console redesign qualification

The connected console implements the supplied design handoff on the living-tenant
codebase. No framework/build migration, external asset dependency, or real Microsoft
tenant is involved. The root checkout and its running stack were not changed.

## Delivered behavior

- Forest-teal/lime shell, light/dark system themes, responsive navigation and seven
  service-oriented screens. The optional Inspector remains server-controlled.
- Complete existing family catalog, bounded browsing, current-page search, scoped
  detail drawers, server-reported relationships, raw JSON and content downloads.
- A workbench separating administrative fixtures from named-identity public API
  requests. User/group GET and PATCH reuse existing Graph v1.0 providers and their
  schema/authorization checks. Other reads reuse curated local resource paths.
- Signed previews bind tenant, family, mutation/version and administration session.
  PATCH retains omitted properties; replacement requires independent consent and
  removes omitted fields according to the adapter contract.
- Three-step composition, stable live inputs, ordered/inherited layers, JSON/YAML
  import, export, saved revisions and optional console defaults. Existing tenant
  selection is explicit and is not embedded in reusable templates.
- Durable population history, tenant-bound status/commands/reports, real cancel and
  resume, immutable public configuration and downloadable manifest/ground truth.
- Hunting with named identities, typed results, a 200-row display limit and full
  returned-result export. Following View evidence preserves the Hunting identity
  and starts no query. Unsupported queries return actual backend errors.

## Contracts and bounded scope

`GET /_mock/resources/catalog` is the existing fixture/action inventory.
`GET /_mock/console/workbench/catalog` adds curated reads and directory updates;
`POST /_mock/console/tenants/{tenant}/resources/{family}/read` dispatches only those
curated paths. It cannot accept arbitrary origins, methods or headers. Mutations use
existing `/resources/{family}/preview` and `/apply` contracts. The optional
`tenant_id` query parameter on run routes prevents a selected run being relabelled
under another tenant; administrative bearer clients retain their existing access.

The Graph update subset is the existing `USER_UPDATE`/`GROUP_UPDATE` implementation,
not every property accepted by Microsoft. Contract references remain in the UI.
`X-MockAzure-If-Version` is a local concurrency extension, not a Microsoft header.
The public service response remains authoritative for permission and value checks.

Browser uploads are capped at 16 MiB; larger content uses the existing chunked API.
Run step previews show 100 steps; use the steps API for later pages. Browser ground
truth exports stop above 16 MiB and direct the operator to the streaming CLI.
Ground truth contains verified namespace receipts; manifests identify the run.
Protected configuration is not exposed in the run configuration panel. A success
response does not by itself prove an evidence record exists.

## Reproducible automated checks

All checks used the isolated worktree's virtual environment and disposable local
PostgreSQL/HTTPS instance. Browser requests were forwarded through the existing
CA-validating, same-origin bridge to the real API. No canned success responses were
used. The stale-response test delays delivery of genuine responses.

| Gate | Result |
| --- | --- |
| `python -m pytest tests/unit -q` | 941 passed; three existing SDK deprecation warnings |
| Affected `test_population_*`, `test_console_*`, `test_hunting_*`, content-storage integration files | 95 passed, one optional 256 MiB scale check skipped |
| Final workbench session/run binding and package tests | 7 passed, including two newly added isolation tests |
| `python -m pytest tests/browser -q` | 9 passed in 98.96 seconds against the real local HTTPS instance |
| `node --test tests/frontend/state.test.mjs` | 2 passed |
| `python -m ruff check .`, `git diff --check`, all modules `node --check` | Passed |
| `python -m tools.check_cloud_coverage --check` | Passed: 317 public operations / six service groups |
| `uv build --offline --out-dir work/ui-dist` | Wheel and sdist built; all static assets present, no runtime files |

The full repository integration suite and externally downloaded-client workflows
are CI gates; this report does not claim they were all rerun locally. The optional
large-content check is unrelated to the browser's 16 MiB cap.

Browser test entry points:

- `test_console_redesign.py`: screen/theme/size matrix; deep-link reload and drawer
  focus; reader/writer authorization; invalidation; replacement consent; upload
  boundary and real byte download; evidence identity preservation; composition
  save/reload/new-tenant run/downloads; delayed preview and tenant responses;
  YAML imports and inherited-layer integrity.
- `test_lab_console.py`: saved revisions, 60-user population, 50-row paging,
  unsupported payload rejection, HTML escaping, public API readback and logout.
- `test_living_tenant_console.py`: finite activity, cancellation, resume, relationship
  navigation and mode distinction.
- `test_hunting_console.py`: edited KQL, denied identity, HTML escaping, at least
  205 returned rows, 200 displayed rows and full JSON download.

Run these with `MOCKAZURE_BROWSER_CONFIG`, `MOCKAZURE_BROWSER_TENANT_ID` and
`MOCKAZURE_BROWSER_TENANT` pointing to an explicitly disposable synthetic instance.
A separate API process and population worker must run. The test-only password file
stays in that instance's private runtime directory and is never committed.

## Handoff acceptance map

PASS means the named automated check and inspected implementation substantiate the
behavior. It is not a claim of assistive-technology certification. The two manual
checks below remain explicitly BLOCKED; strict handoff sign-off is therefore still
pending those checks. There are no unresolved required backend contracts.

| IDs | Status | Evidence |
| --- | --- | --- |
| BAS-01 | PASS | Wheel/sdist build, packaged-asset inventory, native HTTPS browser suite |
| AUTH-01, AUTH-02 | PASS | `test_console_auth.py`: real login, CSRF, expiry, rotation, shared sessions/logout; browser sign-in/out |
| AUTH-03 | PASS | Installed `console password set --config PATH` help and existing console password/CLI tests |
| NAV-01, NAV-02 | PASS | All-screen navigation, record/run reloads, route-state tests; drawer URL contains IDs only |
| TEN-01 | PASS | Real delayed tenant-response browser test plus per-view abort/generation guards |
| TEN-02 | PASS | Foreign selected-tenant run test; adapter tenant/cursor and inspector isolation regressions |
| RES-01 | PASS | Existing catalog breadth and bounded service browser integration tests; catalog-driven navigation |
| RES-02 | PASS | Actual resource query validation, field-associated JSON errors, retry/empty/loading views |
| RES-03, RES-04 | PASS | 60-user browser workflow, 50-row cursor page, truthful loaded-page search label/filter |
| RES-05, REL-01 | PASS | Drawer raw JSON/reload and real SharePoint related-resource browser navigation |
| UPL-01, UPL-02 | PASS | 16,777,216/16,777,217-byte selection checks; actual upload, content ID use and byte readback |
| OP-01, OP-02, OP-03 | PASS | Catalog-backed operation controls; real reader GET/write denial and writer PATCH; mode reset |
| PRE-01, PRE-02, PRE-03 | PASS | Server diff/receipt checks, browser input invalidation and delayed real preview discard |
| PRE-04, PRE-05 | PASS | Tampered/replayed version rejection, session binding, existing public-action/recovery tests; pending/unknown-outcome handling |
| REP-01, OP-04 | PASS | Consent toggles independently; omitted description removed; malformed/unsupported payload remains editable |
| CMP-01, CMP-02 | PASS | Layer movement/inheritance integrity, stable input/step/skip-link tests |
| CMP-03 | PASS | Saved defaults roundtrip, actual exports, YAML import, source validation and immutable revision tests |
| CMP-04, CMP-05, CMP-06 | PASS | Real previews, revision invalidation, existing/new-tenant population workflows |
| ACT-01 | PASS | Finite activity browser cancel/resume plus existing activity validation/recovery regressions |
| RUN-01, RUN-02, RUN-03 | PASS | Durable run/recovery integration tests; route-bound polling, persisted config and reload browser checks |
| RUN-04, RUN-05, RUN-06 | PASS | Actual cancellation/resume, manifest and namespace ground-truth downloads |
| HNT-01, HNT-02 | PASS | Custom query/denial browser tests; result invalidation and authorization integration suite |
| HNT-03 | PASS | Writer success → View evidence preserves reader; no automatic query, actual 403 on execution |
| HNT-04, HNT-05 | PASS | More than 200 returned rows/export test and actual table catalog/entitlement contracts |
| EVD-01, CLK-01 | PASS | Unknown provenance is explicit; existing projection/evidence/clock tests and runtime-session tests |
| INS-01, INS-02 | PASS | Opt-in/direct request enforcement, curated projections and excluded secret-column tests |
| A11Y-01 | PASS | Labelled controls, native modal semantics, tested opener focus/close, skip navigation; honest navigation buttons |
| A11Y-02 | BLOCKED | ARIA status/error/field associations implemented and inspected; an actual screen-reader session was unavailable in this tool environment |
| UI-01 | PASS | Composer typing/step/caret-preserving structure; run configuration node retained during polling |
| UI-02 | BLOCKED | Automated 320/375/390/900/1440px layouts pass; physical browser zoom and assistive-device review remain manual |
| UI-03 | PASS | Local screenshots inspected against supplied tokens/source; system themes and reduced-motion CSS; no external assets |
| P-01, P-02, P-03, P-04 | PASS | Contract inventory, commands/results, connected workflow tests and screenshots in this report |

Offline design-demo D-01–D-04 are not backend acceptance. The browser blocked the
reference file URL; the supplied HTML source and Markdown handoff were reviewed.
No alternative serving method was used to bypass that block.

Screenshots are generated under `work/ui-browser-qualified/` by the connected
browser tests (desktop, dark/mobile, tablet, record drawer, finite activity and
Hunting). They contain synthetic lab data. For example:
`test_all_screens_themes_and_na0/api-workbench-light-1440.png`.

## Review and remaining release checks

One independent whole-branch review found the additional-parameter deletion bug and
skip-link routing bug. Both were fixed and regression-tested. An inherited-layer
array aliasing problem from the previous implementation was also corrected and
covered. No P1 finding remained.

Before declaring the complete design acceptance checklist signed off, perform the
screen-reader and physical browser-zoom checks above. CI status belongs to the PR;
local results here do not imply that remote CI has completed. No root-checkout
update or production-stack restart is part of this branch.
