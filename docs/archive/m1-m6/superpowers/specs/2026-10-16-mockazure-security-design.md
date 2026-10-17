# M3C security administration and policy design

Status: selected under the user's explicit overnight delegation on 2026-10-16.
This refines the approved M3 delivery map. Native execution is retained; routine written-spec/plan approval pauses are waived by the user's explicit instruction, not inferred from silence.

## Intent and approach
Make security-tool Graph queries observe the same persistent identities, grants, roles and policies that govern local authentication. Administrative mutations must create auditable facts. No scenario is necessary.
Extend shared identity and evidence services instead of creating parallel fixture-only routes. Alternatives were a generic JSON endpoint store (inadequate enforcement) and a broad Microsoft policy replica (unbounded/unsupported claims). Choose normalized operational identity state with typed supplementary resources and a declared policy evaluator.

## Resources and wire contract
Use Graph v1.0 structural metadata at existing pinned revision34b8e9c29032c82352a6fd7138be0a864a842bb2; extract application,servicePrincipal,device,passwordCredential,keyCredential,appRoleAssignment,oAuth2PermissionGrant,unifiedRoleDefinition,unifiedRoleAssignment,directoryRole,authenticationMethod and concrete methods,conditionalAccessPolicy,namedLocation and dependencies. REST operation references determine permissions and behavior, not CSDL alone.

- GET/POST applications; GET/PATCH/DELETE applications/{objectId}; appId alternate key; POST addPassword/removePassword; keyCredentials certificate updates. Graph objectId must differ from OAuth appId and remain stable after restart/migration.
- GET/POST servicePrincipals; GET/PATCH/DELETE servicePrincipals/{id}; appId alternate key. Share enabled identity, names, credentials/grants and managed-identity type.
- GET/POST devices; GET/PATCH/DELETE devices/{id}; registeredOwners reads and explicit reference mutations. These are directory devices, not live Intune agents.
- applications/SP owners: GET, bounded expand, POST $ref, DELETE owner/$ref; validate tenant-local users/SPs and ownership permissions.
- oauth2PermissionGrants list/item/create/update/delete, including Principal and AllPrincipals consent; clientId/resourceId are SP object IDs, principalId is user ID, scope uses actual delegated permission values.
- servicePrincipals/{id}/appRoleAssignments and appRoleAssignedTo list/create/delete; actual appRoleId GUIDs map to the supported permission catalog. Existing administrative grants remain visible and operational.
- roleManagement/directory/roleDefinitions and roleAssignments inspection and assignment mutation; directoryRoles and members legacy inspection. Stable template IDs, effective existing roles, tenant scope '/', no false custom-role/PIM support.
- users/{id}/authentication/methods and concrete method collections inspection. Password/phone/email/Authenticator/FIDO2 descriptors must reflect stored enrollment, never export passwords, private keys, OTP seeds or invented successful challenges.
- identity/conditionalAccess/policies CRUD and namedLocations CRUD for IP named locations. Source schemas and enum values remain official.

Lists support declared scalar $filter (eq/ne/in/startswith as appropriate), $select,$top, stable continuation and permitted $count/$orderby. Reuse typed parser and authorization. Unknown query features fail explicitly; no silent ignoring. Owners support bounded $expand with no unbounded relationship fanout. Snapshot cursor binding includes tenant,resource,caller/permissions and query; reauthorize every page.

## Shared state and atomicity
Application Graph IDs/profile fields extend identity_applications; OAuth client IDs and existing credentials survive migration. Supplementary persisted security resources/relationships have tenant keys and enforce referential integrity in locked transactions. Grant/role Graph mutations update existing normalized effective tables. Resource/catalog service principals are exposed with stable tenant-local object IDs and real resource appIds. Do not synthesize invented permission GUIDs.

Use the existing evidence source/outbox and fenced worker for security mutations. Capture actor and before/after public fields transactionally; never include generated secrets, password hashes or private keys in audit differences. Existing reader behavior and evidence exports remain regression gates.

Credentials are valid only during start/end bounds, revocable through Graph, and secretText is returned once by addPassword. Never return secretText on ordinary reads/export/audit. Existing issued access tokens retain their documented lifetime/claim semantics; new token requests and refresh reevaluate configured grants and current principal state.

## Operation authorization
Per-route application/delegated permissions follow official REST references. Reads do not imply writes. Delegated writes require relevant operational role or explicitly documented owner privilege. Application.ReadWrite.OwnedBy is object-scoped. Grant writes and privilege-sensitive role assignments require their distinct permissions and privileged delegated roles; reject cross-tenant targets and non-'/' role scopes. Extend the supported catalog only from authoritative permission IDs/names. CA mutations require Policy.ReadWrite.ConditionalAccess plus the documented read permission combination; delegated CA Administrator/Security Administrator or Global Administrator. P1 entitlement is required for supported CA operations/evaluation; write behavior must not pretend enforcement when unlicensed.

## Conditional Access profile
Persist enabled,disabled,enabledForReportingButNotEnforced policy states.
Supported targets: explicit user/group inclusion/exclusion (including All), explicit resource appIds or All, and client app category all/browser/mobileAppsAndDesktopClients where determined by the local flow.
Supported conditions: IPv4/IPv6 CIDR named locations and All/AllTrusted location selectors, explicit include/exclude semantics. Peer IP comes from the actual connection, never untrusted forwarding headers. An administrative synthetic-input interface may supply lab location/context only through authenticated control plane.
Supported grants: block and mfa, with validated AND/OR combinations within this set. Block takes precedence across enabled matching policies; exclude takes precedence within a target. Report-only evaluates/results but never blocks/prompts. Disabled never applies.
Unsupported risk/device/platform/session/authentication-strength/terms-of-use conditions or controls are rejected for enforced policies; inspection/import must identify unsupported state, never claim a silent allow. Explicit REST error and capability boundaries document this.
Evaluate after user authentication, before code/device approval and at code redemption/refresh so policy edits cannot be bypassed by a preexisting pending flow. MFA complete passes; skip cannot pass an enforced MFA requirement. Report outcomes in signIn conditionalAccessStatus/appliedConditionalAccessPolicies and authentication details, with permission-based disclosure. Token issuance and audit evidence share the same decision.

## Validation
Meaningful end-to-end tests: app creation -> addPassword -> real token -> grant -> real Graph access -> revoke -> new token denied; role assignment alters delegated operations; owner privilege cannot cross objects/tenants; CA block/MFA/report-only with groups/resources/locations and refresh/pending-flow changes; original immutable audit facts and public evidence.
Real MSAL/Graph Python and Graph PowerShell requests cover application/delegated routes and errors, not replacement client cmdlets. Preserve M1/M2/M3A/M3B regression coverage. Fresh clean-wheel/container qualification and source/artifact hashes; one final independent review then one verified fix pass. Functional profile does not imply full Graph or proprietary risk/CA equivalence.

## References
- [Applications](https://learn.microsoft.com/en-us/graph/api/resources/application?view=graph-rest-1.0)
- [Add password](https://learn.microsoft.com/en-us/graph/api/application-addpassword?view=graph-rest-1.0)
- [Service principal](https://learn.microsoft.com/en-us/graph/api/resources/serviceprincipal?view=graph-rest-1.0)
- [Delegated grant](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-post?view=graph-rest-1.0)
- [Application grant](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-post-approleassignments?view=graph-rest-1.0)
- [Directory role assignments](https://learn.microsoft.com/en-us/graph/api/rbacapplication-post-roleassignments?view=graph-rest-1.0)
- [Authentication methods](https://learn.microsoft.com/en-us/graph/api/resources/authenticationmethod?view=graph-rest-1.0)
- [Conditional Access](https://learn.microsoft.com/en-us/graph/api/resources/conditionalaccesspolicy?view=graph-rest-1.0)
- [CA update permissions](https://learn.microsoft.com/en-us/graph/api/conditionalaccesspolicy-update?view=graph-rest-1.0)
- [Permissions](https://learn.microsoft.com/en-us/graph/permissions-reference)
