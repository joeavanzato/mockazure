# Console redesign execution plan

Spec: docs/design/console-redesign/{DESIGN_SYSTEM_HANDOFF,ACCEPTANCE_CRITERIA,IMPLEMENTATION_PROMPT}.md. Visual reference: D:/projects/mockazure/designhandoff/MockAzure-Interactive-Mockup.html.

Goal: implement the supplied connected console across existing service catalogs using packaged HTML/CSS/ES modules and Python/FastAPI/PostgreSQL. Execute inline using superpowers:executing-plans and one final independent review.

## Decisions
- User approved the living-tenant base and scoped adapters for existing APIs. Branch: feat/console-redesign. Root stack and VTW untouched.
- Supplied design and implementation prompt are the authorized specification; preserve existing Native execution preference.
- Missing separate tokens/brief are covered by the supplied main document and embedded HTML. Browser local-file navigation was blocked; review source instead.
- No framework migration, external assets, real Microsoft tenant calls, sample data masquerading as backend responses, or privileged fallbacks.
- Keep signed previews, server authorization, tenant context, existing upload limits, persisted revisions/runs, optional Inspector and evidence-clock separation.

## Tasks
- [x] Shell and shared state: design tokens/themes, accessible shell/login, hash routes, context cancellation, revision guards. Tests: route roundtrip and obsolete response invalidation, packaged assets.
- [x] Explorer/workbench: catalog-driven service/family rail, honest loaded-page search, paging, detail drawer/related/JSON, all existing operations, signed preview/apply, consent and uploads. Narrow API adapter tests cover directory GET/PATCH authorization and concurrency.
- [x] Compose/runs: stable three-step draft and summary, full source/revision/import/export, server preview, finite activity, tenant-bound run history/configuration/cancellation/recovery.
- [x] Hunting/Inspector: reference rail, typed query result/export invalidation, optional read-only inspector.
- [x] Qualification: isolated real local backend/browser checks; both themes, desktop/tablet/320/390px; acceptance evidence map; final review and fixes.

Every task: failing behavioral tests before implementation, focused regression checks, coherent commits. Keep run/tenant IDs and request contexts explicit. Never reset focused inputs on summary/poll updates.

## Review focus
Late responses; consent versus proposal edits; history/deep links without replaying mutations; source/parameter roundtrips; narrow layout and keyboard accessibility.

## Ledger
- Baseline 18 unit tests passed (console packaging, population catalog, profile resolution).
- GitHub CLI currently returns HTTP 401; credentials never printed.
- Existing preview receipts bind tenant/family/mutation and expected version for one hour. Directory fixture import is create-only, not replacement.

- Implementation and automated qualification complete; see docs/console-redesign-results.md for all commands/results and the two pending manual acceptance checks.
- Independent review findings fixed: parameter deletion, skip-link route reset, inherited-layer aliasing.
- GitHub access restored. PR35 is merged; origin/main has the same source tree as the approved base.
