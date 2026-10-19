# MockAzure lab guide, composable profiles and management console

Date: 2026-10-19

Status: written specification approved by the user on 2026-10-19. Implementation plan prepared for review; no implementation is claimed.

Source baseline: cloud qualification commit `68a6d10dcda27c836201495f0bbfd6277afa22c1`. This does not imply that the cloud feature stack is merged into main. Reconcile the implementation base with the remote before starting code changes.

## 1. Outcome and agreed decisions

A developer or analyst can deploy a local or shared MockAzure lab, compose reusable tenant and data profiles, populate connected service data, and browse or edit that data without understanding the database schema. Realistic API emulation remains the product; profiles are reusable test setup, not a replacement for operational state.

The user agreed to:

- A repository Markdown guide for deployment and ordinary use.
- A dynamic population tool shared by the CLI and browser.
- Tenant profiles with inheritance, ordered data-profile overlays and reusable composition templates; no copy-paste requirement for common definitions.
- Later layers override explicitly supplied properties. Conflicting definitions of the same resource require an explicit override.
- Service-oriented browsing and editing, plus an optional read-only database inspector.
- Shared-lab access with localhost as the default and simple password authentication. Avoid SSO, individual accounts and a new role system.
- A frontend packaged with MockAzure and served from its existing HTTPS origin.

Preserve PostgreSQL, Docker and native Python deployment, Windows/PowerShell support, Apache licensing and the existing Microsoft contract constraints. Never connect execution to a real Microsoft tenant. Do not modify VTW or import its untracked files, secrets or generated certificates.

## 2. Existing foundation and architecture

The repository already supplies Docker initialization, generated TLS material, CLI administration, a FastAPI administration API, persistent background jobs, typed dataset imports and service implementations. `admin/auth.py` verifies an instance bearer token; `admin/session_routes.py` manages simulated OAuth sessions, not administrator browser sessions. Keep these distinctions.

Reuse the validated models and domain operations behind directory, evidence, workload, mail, security and cloud administration routes. Existing cloud state PUT operations can replace state: they must not silently become an append mechanism. Extract useful generators from `tools/scale_dataset.py` and the service fixture helpers into packaged modules where appropriate; never import qualification runners that start containers, reset labs or execute collectors.

Add three focused components within the existing application:

1. A profile resolver and generator library: declarative inputs to a validated, deterministic execution plan.
2. A resource catalog and execution adapters: supported browsing, provisioning, service actions and verification for each family.
3. A small browser console using the same management APIs and population engine as the CLI.

Use the existing PostgreSQL/job infrastructure for runs and checkpoints. No new queue broker or frontend server. Prefer modular HTML/CSS/JavaScript assets for this initial console; package them in wheels, source distributions and Docker images. Runtime operation must not require Node, a CDN or network access.

## 3. Profile model and composition

### 3.1 Documents

Use versioned YAML documents with a JSON equivalent. Parse data only: no executable expressions, arbitrary YAML constructors, shell hooks, remote includes or runtime package loading. Validate document size, nesting and total resolved input size.

- **Tenant profile:** structural configuration such as departments, identities, memberships, licenses, applications, permissions, subscriptions and service resources.
- **Data profile:** related content, historical observations or explicit operational steps targeting resources from the tenant and earlier layers.
- **Template:** selects a tenant profile, ordered data profiles and parameter defaults. Templates can inherit from another template.

Each profile has a stable name, format version, optional single parent (`extends`), parameter definitions and typed resource/generator/step entries. Data profiles can also inherit from another data profile. Named parameters can be referenced and passed into generators; there is no general expression language. Explicit dependencies and symbolic resource references provide cross-service joins.

Illustrative composition (the implementation plan will define the complete schema):

```yaml
schemaVersion: 1
kind: template
name: investigation-lab
tenantProfile: midsize-business
dataProfiles:
  - baseline-business-activity
  - exchange-mail-and-audits
  - sharepoint-and-teams
  - suspicious-signins
parameters:
  seed: 42
  referenceTime: "2026-10-01T00:00:00Z"
  days: 30
```

### 3.2 Resolution and precedence

Resolve parent before child, tenant before data layers, and data layers in listed order. Reject inheritance cycles, missing references, ambiguous names and dependency cycles. A dependency orders an already selected step; it does not silently install an unselected profile.

Parameter precedence is profile defaults, inherited template values, child template values, then explicit run overrides. Namespace profile-specific parameters; explicitly declared shared parameters such as seed and reference time are shared. Report the origin of each resolved value.

Merge rules are deterministic:

- Omitted properties inherit. Explicit scalar properties override earlier values.
- Configuration maps merge by key. Lists replace as a whole; never guess whether array items represent a set or an ordered sequence.
- Resources and steps use stable logical keys rather than array position. Identical repeated definitions deduplicate. A different definition of an existing key requires an explicit `override: true` entry; its supplied properties patch the inherited definition.
- Explicit null is permitted only when the underlying contract allows null. It is not a deletion instruction.
- Deletion/reset is a separately declared operation, visibly destructive in preview. Overlays do not implicitly delete previously generated resources.

After merging, validate the complete result again. An explicit override permits composition; it does not bypass Microsoft field validation, permissions or runtime concurrency checks.

### 3.3 Identity, targets and reproducibility

A template is a blueprint, not a tenant identity. A run explicitly selects an existing tenant or requests creation of a named tenant from the resolved profile. Do not infer a new tenant from a seed or target the current tenant silently.

Derive supported client-assigned IDs from tenant identity, dataset namespace, logical key and seed. Capture service-assigned IDs in the run manifest and use them for subsequent references. Shared references connect users, devices, mailboxes, Teams membership, storage accounts, evidence subjects and subscriptions.

Determinism requires a fixed seed, reference time, generator version and resolved profile. Capture any convenient `now` choice once before preview. Determinism describes intended generated data; actual service actions can produce service timestamps, IDs and evidence according to existing contracts. Never promise byte-identical live operation results.

A stable dataset namespace allows replay without duplication. An explicit new namespace supports another independent dataset. Changing generation parameters within an existing namespace produces a previewed change/conflict, not silent replacement or deletion.

## 4. Population execution

Expose packaged `mockazure profile` and `mockazure populate` command groups for listing/validating profiles, previewing compositions, applying them, inspecting runs and resuming eligible work. Supply a thin executable example script that calls this packaged implementation; the script is not a second generator. CLI and UI submit the same normalized request to the authenticated administration API. Offline validation can run without an instance; state-aware preview requires one.

The server stores uploaded profile bundles as data. Includes resolve only within the bundle or installed profile catalog. Execution destinations come from the instance's configured local service endpoints, never from profile-supplied arbitrary URLs. Content inputs are explicitly uploaded bytes or bounded generated content, not server filesystem paths or URL downloads.

Execution stages:

1. Resolve definitions and dependencies; validate parameters and capabilities.
2. Preview intended creates, patches, replacements, resets and required permissions, with bounded samples and estimated counts/bytes. Do not materialize a million-record dataset in memory.
3. Freeze resolved inputs, generator versions and preview fingerprint in a run manifest. Check relevant generations/revisions again at execution; stale affected state requires a refreshed preview or fails explicitly.
4. Execute bounded batches in dependency order using validated service adapters.
5. Wait for applicable asynchronous publication with bounded timeouts; verify representative reads and expected counts where supported.
6. Persist outcomes, logical-to-actual IDs, checkpoints, errors and verification results. Never store credentials in exported manifests.

Two explicit step types preserve meaning:

- **Fixture provisioning/import:** establish tenant state or historical evidence through supported administration operations, including evidence that Microsoft exposes only as read-only resources.
- **Service action:** call the emulated public API using a selected local application/user identity and its actual permissions. Administrator access never bypasses authorization for these calls. Permission failures are reported; the engine does not silently grant access.

Verification distinguishes administrator existence checks from reads as the selected test identity. A successful import alone is not proof of collector access. Unsupported operations fail during preview wherever discoverable, rather than falling back to direct database writes.

### 4.1 Replay, failure and concurrency

Persist a run and its checkpoints before executing batches. Reuse the worker claim/fencing pattern for restart recovery. Serialize population runs for the same tenant initially; permit independent tenants to run concurrently. Ordinary API traffic remains possible and is handled through service preconditions and conflict detection, not a global application lock.

Prefer stable identifiers, existing import idempotency and read-before-reconcile behavior where the adapter can prove it safe. Compare expected state before skipping a completed step; missing or conflicting data must not be reported as success. Non-idempotent actions with an uncertain outcome after interruption are marked `needs-attention` unless their result can be reconciled. Do not blindly resend mail, enqueue duplicate messages or repeat irreversible actions.

Cross-service runs are not atomic. Preserve successfully applied work and identify failed/blocked steps. Resume only eligible steps; stop/cancel at batch boundaries. No general rollback system. Destructive cleanup uses explicit existing lifecycle/reset operations with their generation checks and clearly stated scope. Unrelated data must survive additive runs. A bulk replacement adapter is permitted only when replacement is explicitly selected and its complete affected scope is shown.

## 5. Service coverage and reusable starter content

Build a catalog covering the implemented service families on the chosen implementation baseline. Each entry records display fields, relationships, paging, supported read/write/import operations, validation source, API path/version, and limitations. Reuse operation/schema inventories but add explicit curated adapters: an operation appearing in a coverage file does not imply a usable form or a generator.

| Family | Population and browsing scope |
| --- | --- |
| Entra/directory/security administration | Users, groups, devices, applications, grants, roles, licensing and supported authentication/policy configuration |
| Graph and security evidence | Sign-ins, directory audits, risk, supported alerts/incidents/scores and audit-query evidence |
| Mail/Exchange | Mailboxes, supported mail/calendar content, rules, delegation, audit records and supported security/configuration objects |
| SharePoint/OneDrive/Teams/Intune | Sites, drives/items/content, supported Teams objects, membership and managed-device/configuration inventory |
| Activity feed | Supported subscriptions, content and records with coherent subjects/timestamps |
| Azure management | Subscriptions, resource groups, providers, compute/network inventory, permissions, storage accounts and diagnostic settings |
| Storage | Blob containers/content, Files shares/directories/files, Table entities and Queue messages through implemented REST semantics |
| Key Vault | Vault relationships and synthetic secrets through supported lifecycle operations |
| Queries and diagnostics | Log Analytics workspaces/rows, Activity Logs and diagnostic workflows; Resource Graph queries derived from resource state |

Every existing implemented family needs a catalog entry and a truthful browse/provision route or an explicit unsupported action. A family with missing administrative paging may receive a bounded read API; do not download its whole export on every screen. Add small administrative access gaps as needed, but do not invent unsupported public Microsoft endpoints as part of the console.

Starter templates: general developer lab, evidence collection lab, investigation lab, and configurable volume lab. Compose these from common tenant profiles and reusable per-service data profiles. Include ordinary activity and optional suspicious activity with exported expected facts/ground truth. No proprietary detection or risk-engine claims. Each template declares exactly which families and relationships it covers; the combined examples exercise every row above within implemented limits.

Derive operational field validation from existing pinned Microsoft contracts, especially Graph v1.0 request/response distinctions. New public operations, if needed, require their official contract references before implementation. Administrative profile metadata stays outside Microsoft payloads.

## 6. Browser console

Serve the console under `/_mock/ui/` on the existing HTTPS origin. Use a persistent tenant selector and service navigation, with a clear empty state and links from each resource family to its supported API path and coverage notes.

- **Resources:** server-paged tables, supported filters, JSON details and relationship navigation. Forms for common required properties plus a validated JSON editor for advanced input. Upload/download content where supported. Unsupported edits are visibly unavailable. Show whether an operation is administrative provisioning or a public API action and which identity will execute it.
- **Profiles & Templates:** choose, import, compose and save versioned profiles; edit parameters and layer ordering; inspect inherited values, explicit overrides and the final resolved preview. Provide a structured composition form and a text editor, not a graphical workflow designer.
- **Runs:** start, inspect progress, see sanitized errors, cancel at safe boundaries, resume eligible work and export a manifest/ground-truth report. Simple polling is sufficient initially.
- **Database inspector:** disabled by default and explicitly enabled by configuration. Browse approved tables/columns using server-controlled parameterized queries and bounded paging. No arbitrary SQL, writes or unrestricted database export. Exclude authentication secrets, keys, bearer tokens and credential-bearing JSON columns using an allowlist, not only column-name heuristics. Show tenant scope and distinguish shared system metadata.

Resource browsing should not consume queue messages: use existing peek semantics. Secret-value display belongs to an explicit authenticated service operation, not table previews, manifests or the inspector. Synthetic secret data must not cause disclosure of the lab's own credentials.

Prioritize a usable desktop layout with keyboard-accessible controls, readable tables and clear progress/error feedback. No dashboard chart suite, portal clone, elaborate visual profile builder or additional navigation framework is required.

## 7. Shared-lab authentication and deployment

Use one shared administrator password. This means a password login form, not browser-cached HTTP Basic credentials. Existing bearer-token CLI administration continues to work. Administrator browser sessions are separate from the simulated users and OAuth sessions.

Initialize the password through an interactive prompt or protected mounted file. Store a salted password hash, never the plaintext. Use standard maintained password-hashing primitives, selected and pinned during implementation. Avoid command-line secret arguments and logging submitted credentials.

Store hashed opaque session identifiers with expiry in PostgreSQL so multiple API instances can share sessions. Use Secure, HttpOnly, SameSite cookies, CSRF protection for cookie-authenticated mutations, server-side logout and password-change revocation. Set an eight-hour absolute session expiry. Basic login throttling must work across API replicas and avoid permanent lockouts. There are no per-person permissions or attribution claims with a shared password.

Session authorization grants access only to the management interface. It must not become a Microsoft access token or authorize arbitrary public API calls. A server-side action adapter can obtain credentials for explicitly selected local test identities; never hand the instance administrator bearer token to browser JavaScript.

Default published administration access to loopback. Shared deployment requires explicit bind/advertised-host configuration and matching TLS names. Browser traffic uses one origin; do not enable wildcard CORS. PostgreSQL is not newly exposed to the LAN for this feature. Document CA trust on teammate machines and preserve Docker-internal service connectivity when changing published host binding.

Include assets, built-in profiles and the generator in every distribution. No remote profile registry, external identity provider, email invitation flow or automatic certificate service.

## 8. Deployment and usage guide

Write `docs/getting-started.md`, linked prominently from README and the documentation index, plus `docs/profiles.md` for the profile reference. Include:

1. Requirements and a clean Docker deployment using Windows PowerShell, with Git Bash notes and native Python guidance.
2. Initialization, persistent volumes, TLS trust, readiness checks and administrator password setup.
3. Localhost defaults and the explicit changes for a shared LAN lab.
4. A complete tenant-profile plus stacked-data-template example, preview, apply and verification from both CLI/script and UI.
5. Connecting an application using local endpoints, CA and test identity permissions; contrast admin provisioning credentials with application credentials.
6. Resource browsing, manual data creation, profile inheritance/override rules, repeat runs, resume and data-reset scope.
7. Backup/restore pointers that include database and required instance keys; profile/manifests are not full backups.
8. Troubleshooting TLS, endpoint/hostname mismatch, permissions, unpublished evidence, worker health, stale previews and partial runs.
9. Service coverage and limitations, linking to `COVERAGE.md` rather than duplicating its full endpoint inventory.

Commands in the final guide must be executed against a disposable local lab before being described as tested. This spec does not claim that proposed commands or UI routes exist today.

## 9. Acceptance and scope boundaries

Verify meaningful behavior rather than implementation details:

- Profile inheritance, precedence, list replacement, explicit resource override, invalid references/cycles, null semantics and stable resolution across CLI/UI.
- Deterministic generated relationships and time windows, additive rerun without duplicates, intentional separate datasets and no silent deletion.
- Per-service imports/actions/readback using pinned contracts, including permission failures and read-only Microsoft evidence provisioning.
- Checkpoint recovery after worker restart, stale-state rejection, uncertain non-idempotent action handling, same-tenant run serialization and partial failure reporting.
- Bounded generation and paging with a representative large dataset; no need to repeat the prior entire cloud qualification suite for a documentation-only change.
- Password/session expiry, logout, CSRF, throttling, host restrictions and credential exclusion in logs, profiles, manifests and inspector output.
- Browser walkthrough: login, compose template, preview/apply, observe completion, browse linked data, add an item, retrieve it through its public API and inspect a validation error.
- Packaged wheel, source distribution and Docker contain working assets/profiles; execute the documented first-run path and a second-client shared-lab path locally.

Update coverage documentation for actual added routes and limitations; do not promote implemented endpoints to fully qualified solely because they appear in the console. Keep accepted upstream collector limitations visible.

Out of scope: production-grade multi-user access control, SSO, arbitrary code execution in profiles, unrestricted SQL tooling, universal Microsoft API parity, SMB/NFS, a generic workflow programming language and cross-service transactional rollback.

## 10. Handoff

This written spec was approved by the user on 2026-10-19. The implementation plan is [Lab Profiles and Console](../plans/2026-10-19-lab-console.md), covering catalog/generator coverage, migrations, tests and packaging work. The implementation plan must reconcile the actual Git baseline first and retain the smallest practical task grouping. Existing qualification results remain bound to their original source commit.
