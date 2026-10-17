# MockAzure M3: Stateful Graph delivery map

**Status:** M3A completed and merged to main in PR #4 at 4a5fedb on 2026-10-16. M3B design is approved and its implementation plan is proposed for review; M3C remains planned.
**Parent specification:** [Approved product design](../../../../superpowers/specs/2026-10-16-mockazure-design.md), especially sections 6–10 and 13–14.
**Execution:** Native: the main agent implements, with one independent whole-change review at each implementation PR boundary.

M3 makes local Graph requests operate on persistent tenant state. It does not require an attack scenario: scenarios remain optional reproducible test inputs. Successful supported mutations change directory state, delta output and, once M3B is implemented, directory-audit evidence.

## Baseline and integration

PR #1 merged to main at a7b6ae469f77d7e20bd8dbd14d3a12c1f934f3c6. PR #2 merged to feat/foundation at a3074f69b16f7abcc76dff93de518c722c159348; it did not advance main. The latter tree is identical to the final verified M2 tree at 27c41788acda33fe7faa758bbe6a0f5834c9c1bd.

M2 integration PR #3 and M3A PR #4 are now merged. Main at 4a5fedb contains both verified increments. Preserve their runtime instances while planning and implementing B.

M2 verification remains in [identity-execution.md](../../identity-execution.md). Its results establish the baseline; they are not evidence for M3 completion.

## Three bounded increments

| Increment | Working deliverable | Exit evidence |
| --- | --- | --- |
| M3A: Directory, queries and delta | Persistent users and security groups, membership and owner reads, selected lifecycle writes, SQL-backed queries, stable paging and user/group delta | Real Python and PowerShell SDK create/read/update/delete/membership/delta round trips; 5,000-user collection across concurrent mutations and replica restart |
| M3B: Evidence and collector output | Sign-ins of all four agreed types, directory audits, risk inspection, durable evidence projection, tenant licences and evidence availability/retention | Original pinned Hawk user sign-in export and MES sign-in/directory-audit exports contain expected IDs and fields, with denied/licence/throttling/restart cases |
| M3C: Security administration and policy | Applications/SPs/devices and owner inspection, grant and role inspection/mutation, application credentials, authentication-method inspection, supported conditional access | Graph administration changes token issuance/authorization; enforced block/MFA policy alters local delegated authentication; report-only and unsupported-policy behavior proven |

All three increments are required to complete M3. A directory-only release is an intermediate result. M3C may be split into smaller plans if its contract becomes too large; that does not remove any rows below.

Exchange mailbox/rule state and UAL queries remain M4. ARM inventory/Activity Logs remain M5. Defender/Purview breadth beyond the approved first-release operations requires later design; do not claim it from generic Graph routing.

## Scope ownership

| Approved requirement | Owner and boundary |
| --- | --- |
| Users, groups, direct/transitive membership; group owners | A: read supported fields; user lifecycle and ordinary static security-group mutations |
| Organization and /me | A: preserve existing M2 behavior and align user reads with shared identity rows |
| Devices, applications, service principals and owners | C: real stored resources and relationships; application Graph id distinct from OAuth appId |
| Application credentials and delegated/application grants | C: normalized external IDs and permission GUIDs, linked to existing credentials/grants; changes affect OAuth |
| Directory roles and role assignments | C: stable Graph identities over effective role state; target-role hierarchy checks |
| Tenant subscribed SKUs, user licence details and assignments | B: stored licence state; permission/licence restrictions for evidence; no real subscription provisioning |
| Sign-ins, directory audits, risky users and risk detections | B: persisted evidence and documented schemas/versions; occurrence vs availability |
| Authentication-method inspection | C: honest stored method types; never expose credentials |
| Conditional access inspection/enforcement | C: delegated user/group and target-resource inclusion/exclusion, IP/location, block and simulated MFA; explicit supported condition/control matrix |
| Query AST, select, ordering, count, top and continuations | A: typed shared mechanism with per-route/property allowlists; B/C extend contracts |
| Bounded expand | A: group members; C: application/SP owners |
| JSON batch | A: independent GET requests only; other methods/dependencies explicitly unsupported |
| User/group delta | A: selected fields, membership changes, tombstones, snapshot rounds, expiry/resync |
| Durable mutation/evidence boundary | A: transactional public change history and evidence outbox; B: worker dispatch/projection and audit endpoints; C: reuse for security mutations |
| Throttling and deterministic faults | A: shared persistent limits and scoped opt-in 429 fault; B: actual collector retry checks |
| Import/export, optional test fixtures, provenance | A: safe versioned directory import/export; B/C extend schema without including credentials/private keys/sessions |
| Multi-replica persistence, restart and SQL-bounded access | Each increment tests its own behavior; M6 qualifies the larger 100k-user/1m-evidence profile |
| Native CLI/package and Docker parity | Every increment; isolated M3 deployment preserves M1/M2 |
| Both collector families | B: Graph output gates; M4/M5 retain remaining release workflows |

## Shared invariants

Use existing identity users, applications, service principals, credentials and grants as the authentication source of truth. Provider projections must not create a second tenant directory that drifts from login behavior. Security administration work must retain existing OAuth client IDs and public contracts.

Allocate tenant revisions transactionally under a per-tenant row lock. A sequence allocated before commit alone is not a safe synchronization watermark: a later transaction can commit first and cause delta to miss a change. Maintain public object/relationship history with validity ranges so ordinary paging can use an as-of revision without keeping a database transaction open across requests. This is a bounded materialized history, not a redesign as a fully event-sourced system.

Commit each supported mutation, its public change entry and an evidence-outbox record in one transaction. The outbox contains normalized nonsecret operation facts and provenance. Until B supplies projection, A documents pending evidence and does not pretend that audit collection works. B extends the existing export-specific leased job engine deliberately; parsing every job as an ExportSnapshot cannot support multiple job kinds.

Query parsing, field projection, permissions, resource version and cursor context are separate contracts. No arbitrary SQL/JSON paths, expression evaluation, silent unsupported-option omission or whole-tenant Python filtering. Reauthorize continuation requests; do not embed reusable bearer tokens in links.

Keep normal operation useful without fixtures or scenarios. Deterministic test imports and optional local fault controls are administrative features, separated from provider authorization.

## Fidelity boundaries to review

- M3A user writes cover ordinary local member users. Privileged target changes, guest invitations, hybrid synchronization and password-reset workflows remain explicit unsupported operations until a later contract implements them. The create-user profile accepts forceChangePasswordNextSignIn=false; true is rejected rather than ignored.
- M3A group writes cover static, non-role-assignable security groups. Dynamic membership, Microsoft 365 provisioning and privileged group management are not simulated by accepting inert settings.
- A's user/group delta supports its own documented query subset. It is not a wrapper around the collection query parser.
- A's default normal collection page size is 100; its local configurable server cap is 999. Delta uses its own bounded server pages. Cursor lifetime is one hour and delta lifetime seven days in this local profile; these are emulator settings, not promises about all Microsoft services.
- A's snapshot paging guarantees are stronger than Graph's general eventual-consistency guarantee. Document this deliberate local consistency model. Policy and current permission checks always take precedence over resuming an old snapshot.
- C enforces only the published conditional-access subset. Imported inspection-only policies are clearly marked outside the Microsoft response as non-enforcing. Unsupported enforcement settings fail validation; they never silently weaken a policy.
- Runtime clients cannot reach real tenants. Existing approved Exchange bootstrap remains bounded to M2; it does not authorize replacing Graph SDK commands or collector functions with canned implementations.

## Collector contracts for M3B

Keep the M2 manifest pins and integrity checks. Hawk a44536f2b4225dd8ea2e463db88d5db40cfac8c7 uses Get-MgAuditLogSignIn with userPrincipalName and createdDateTime filters, follows pages and writes its existing output formats. Verify normal and risky-record output contents.

Microsoft Extractor Suite 47565f7842784cd3667ff630e3dccb49fa3408ed queries beta signIns and v1.0 directoryAudits. Its filters include time comparisons, signInEventTypes/any with OR terms and startsWith(userPrincipalName,...). Preserve its collection and serialization logic. A narrowly scoped endpoint transport adapter may rewrite its hard-coded Graph origin to the local configured endpoint while preserving path, query, method and body; unknown destinations fail closed.

Expected output IDs/fields must come from fixtures and runtime actions, not from a substitute collector. The reported 429 retry result must describe what each original client actually does. A client failure is a compatibility finding, not permission to alter its collection algorithm.

## Optional real-service reference checks

The user has offered an application in a dedicated test tenant for checking real Graph queries. This is a useful future fidelity input, not a prerequisite for M3A and not a change to the emulator's no-upstream runtime boundary.

Before using it, agree the exact test tenant, read-only routes, permissions and retained output. Run reference queries through a separate explicitly invoked harness; never through provider fallback or the ordinary offline qualification suite. Compare normalized response/error/query behavior and keep only sanitized contract fixtures with provenance. No application credentials or raw tenant exports belong in Git. Writes, permission changes and policy enforcement experiments require a separately agreed test scope.

## Review and execution

The first executable plan is [M3A directory/query/delta](../plans/2026-10-16-mockazure-stateful-graph-directory.md). Review that plan together with this map. The detailed B and C plans will be written against the interfaces delivered by A; no approval here implicitly approves implementation of unspecified B/C details.

Approval of A retains Native execution and authorizes its implementation, verification, coherent commits and a reviewable PR. It does not authorize merging that PR or changing host trust/network settings.

## M3B design follow-up

The [M3B evidence design](2026-10-16-mockazure-evidence-design.md) develops the next increment. The user clarified that missing endpoints should be implemented or simulated where necessary for realistic API calls. B therefore includes a working local managed-identity token profile, direct user licence assignment and risky-user confirmation/dismissal, with persistent effects and evidence. Historical imports supplement these live flows. The written design is approved. Its [detailed implementation plan](../plans/2026-10-16-mockazure-evidence.md) and API contract are ready for review before Native implementation.
