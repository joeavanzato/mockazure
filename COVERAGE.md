# API coverage

Endpoint map for **MockAzure**, reviewed on **2026-10-18** against implementation
snapshot [`0ceb9db`](https://github.com/joeavanzato/mockazure/commit/0ceb9db53ed5d2664c726606ae2a54e64cf3cfb8).
The table describes the source tree containing this file. The runtime matches the
qualified coverage expansion at `c418245`; see [qualification results](docs/coverage-expansion-results.md)
and [the coverage audit](docs/coverage-audit.md) for evidence, corrections and limits.
Microsoft links describe the real API; the support and limits columns describe
MockAzure. This is an operation map, not a claim of complete Microsoft service parity.
No real tenant is needed or contacted.

## Reading the tables

- **Implemented**: the listed methods function within the stated local profile.
  This does **not** mean every Microsoft property, permission variant or query works.
- **Partial**: a material restriction within the named surface.
- **Unsupported**: no implementation of the named operation.
- **Historical**: a working compatibility endpoint retained for older collectors.
- Methods apply only to the indicated path. `/{id}` means an item, not arbitrary children.
- Graph tables show **v1.0** paths. **Beta = Yes** means the listed operations also exist
  under `/beta`, not that every beta-only field/operation works. **No** means no beta route.
- `{name}` denotes a path parameter; `[/{id}]` denotes an optional item suffix only where shown.
- Unlisted operations are **not claimed as supported**. Gap tables are priority examples,
  not an exhaustive catalog of Microsoft's entire API.

### Microsoft origins and local equivalents

| Service | Microsoft origin / addressing | MockAzure addressing |
| --- | --- | --- |
| Graph | `https://graph.microsoft.com` + path below | Configured Graph HTTPS origin + same path; default `https://localhost:8443` |
| Entra | `https://login.microsoftonline.com` + tenant protocol path | Configured login HTTPS origin + listed local path |
| ARM | `https://management.azure.com` + path and `api-version` | Configured management HTTPS origin + same path and supported version |
| Management Activity | `https://manage.office.com/api/v1.0/{tenant}/activity/feed` | Configured **activity** HTTPS origin + the same path; separate from ARM management |
| Exchange | Exchange Online PowerShell service transport | Configured Exchange HTTPS origin; pinned module transport below |
| Managed identity | Host-provided `IDENTITY_ENDPOINT` | Separate host-only listener, default `http://127.0.0.1:49446/MSI/token` |

For example, `GET https://graph.microsoft.com/v1.0/users` corresponds to
`GET https://localhost:8443/v1.0/users` in the default local configuration. Microsoft
origins here are references, not emulator traffic destinations. Resource scopes such as
`https://graph.microsoft.com/.default` identify token audiences; they do not select an
upstream server. Use the configured local endpoints and generated CA.

Jump to: [directory](#graph-directory) · [apps/devices](#graph-applications-service-principals-and-devices) ·
[grants/roles](#graph-permission-grants-and-directory-roles) · [methods/policy](#graph-authentication-methods-reports-and-policy) ·
[evidence](#graph-sign-ins-audits-risk-and-licensing) · [security](#graph-security-collections) ·
[mail/UAL](#graph-mail-and-unified-audit-log) · [queries](#graph-query-and-protocol-coverage) ·
[SharePoint/OneDrive](#graph-sharepoint-and-onedrive) · [Teams](#graph-teams-and-chats) ·
[Intune](#graph-intune) · [gaps](#graph-gaps) · [Entra](#entra-authentication) · [Exchange](#exchange-online) ·
[ARM](#azure-resource-manager-and-activity-logs) · [data setup](#populating-data-and-runtime-discovery).

## Graph directory

Directory permissions, delegated roles, limited-information projections and current
identity state are enforced. The [directory contract](integrations/contracts/graph-directory.md)
contains the precise authorization/property allowlists.

| Microsoft REST URI | MockAzure support | Beta | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- | --- |
| `/v1.0/organization` | Partial: GET | Identity fields only | Tenant ID, display name and verified domains; **v1.0 only** adds 16 typed optional contact/address/sync fields provisioned through `directory/collectionState`; `$select`. `User.Read` returns null for the additional fields. No item route or Graph writes; full organization schema remains partial. | [Organization](https://learn.microsoft.com/en-us/graph/api/organization-list?view=graph-rest-1.0) |
| `/v1.0/me` | Implemented: GET | Yes | Delegated identity only; selected supported user fields. No PATCH on `/me`; use the supported user route. | [Get user](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0) |
| `/v1.0/users`<br>`/v1.0/users/{user}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Stateful ordinary local users, selected profile fields, account enablement, local-domain UPN changes and licensing fields. Item reads/writes resolve ID or UPN. No full guest/hybrid lifecycle or restore. | [Users](https://learn.microsoft.com/en-us/graph/api/resources/user?view=graph-rest-1.0) |
| `/v1.0/groups`<br>`/v1.0/groups/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Static security-group writes. No dynamic membership, Microsoft 365 group provisioning or role-assignable group writes. Permanent local deletion. | [Groups](https://learn.microsoft.com/en-us/graph/api/resources/group?view=graph-rest-1.0) |
| `/v1.0/users/$count`<br>`/v1.0/groups/$count` | Implemented: GET | Yes | Declared advanced-query headers required; no universal `/$count` handler. | [Advanced queries](https://learn.microsoft.com/en-us/graph/aad-advanced-queries) |
| `/v1.0/groups/{id}/members`<br>`/v1.0/groups/{id}/transitiveMembers` | Implemented: GET | Yes | Stored membership traversal; hidden-membership and object-read permissions enforced. No arbitrary type-cast path variants. | [Members](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0), [transitive](https://learn.microsoft.com/en-us/graph/api/group-list-transitivemembers?view=graph-rest-1.0) |
| `/v1.0/groups/{id}/members/$ref`<br>`/v1.0/groups/{id}/members/{member}/$ref` | Partial: POST first path; DELETE second | Yes | Add/remove local users or static security groups; cycle checks. No reference-free delete that also deletes a directory object. | [Add](https://learn.microsoft.com/en-us/graph/api/group-post-members?view=graph-rest-1.0), [remove](https://learn.microsoft.com/en-us/graph/api/group-delete-members?view=graph-rest-1.0) |
| `/v1.0/groups/{id}/owners` | Partial: GET | Yes | v1.0 omits service-principal owners; beta includes them. Initial owner bindings and v1.0 standalone owner-reference writes; see the expansion table below. | [Owners](https://learn.microsoft.com/en-us/graph/api/group-list-owners?view=graph-rest-1.0) |
| `/v1.0/users/{user}/memberOf`<br>`/v1.0/users/{user}/transitiveMemberOf` | Partial: GET | Yes | Group traversal with disclosure limits, not general role/administrative-unit navigation. Use explicit role APIs. `/me/memberOf` and `/me/transitiveMemberOf` aliases work in v1.0 with delegated tokens. | [Memberships](https://learn.microsoft.com/en-us/graph/api/user-list-memberof?view=graph-rest-1.0), [transitive](https://learn.microsoft.com/en-us/graph/api/user-list-transitivememberof?view=graph-rest-1.0) |
| `/v1.0/users/delta`<br>`/v1.0/groups/delta` | Implemented: GET | Yes | Durable rounds, selected fields, tombstones and group membership changes. Seven-day local state lifetime, then resync. Other directory families have no delta; mail-message and drive-root delta are listed separately below. | [User delta](https://learn.microsoft.com/en-us/graph/api/user-delta?view=graph-rest-1.0), [group delta](https://learn.microsoft.com/en-us/graph/api/group-delta?view=graph-rest-1.0) |

Delta accepts `delta()`, `microsoft.graph.delta` and `microsoft.graph.delta()` aliases.
Directory mutations publish sanitized audit facts, not merely changes to static fixtures.


### Directory collection expansion (v1.0)

These operation profiles use canonical PostgreSQL tenant state. Beta equivalents are not
implied. Exact methods, route templates, permissions, source hashes and regression tests
are recorded in [the operation manifest](integrations/contracts/coverage-operations.json).
Query, batch and throttling profiles are listed below and in the operation manifest;
the options remain endpoint-specific.

| Microsoft Graph URI | MockAzure support | Notes / unsupported parts | Official reference |
| --- | --- | --- | --- |
| `/v1.0/directoryObjects/{id}` | Partial: GET | Active user/group/application/servicePrincipal/device only; `$select`; tenant-scoped lookup. Unknown/deleted/foreign IDs return 404. | [Get object](https://learn.microsoft.com/en-us/graph/api/directoryobject-get?view=graph-rest-1.0) |
| `/v1.0/directoryObjects/getByIds` | Partial: POST | 1–1000 GUID IDs; mixed types, deduplication and case-insensitive `types`; missing/deleted/foreign IDs omitted. Same five types; no query options. Supports the `microsoft.graph.getByIds` alias emitted by Graph PowerShell. | [Resolve IDs](https://learn.microsoft.com/en-us/graph/api/directoryobject-getbyids?view=graph-rest-1.0) |
| `/v1.0/groups/{id}/memberOf`, `/v1.0/groups/{id}/transitiveMemberOf` | Partial: GET | Group targets only, cycle-safe deduplication; `$select`, `$top`, snapshot paging. No directory-role/administrative-unit navigation. | [Direct](https://learn.microsoft.com/en-us/graph/api/group-list-memberof?view=graph-rest-1.0), [transitive](https://learn.microsoft.com/en-us/graph/api/group-list-transitivememberof?view=graph-rest-1.0) |
| `/v1.0/users/{id}/ownedObjects`, `/v1.0/me/ownedObjects` | Partial: GET | Groups, applications, SPs and devices; ID/type/displayName projection. Unreadable displayName is null; `$select`, `$top`, durable paging. Delegated-only, including explicit `/users/{id}` addressing. | [Owned objects](https://learn.microsoft.com/en-us/graph/api/user-list-ownedobjects?view=graph-rest-1.0) |
| `/v1.0/groups/{id}/owners/$ref`, `/v1.0/groups/{id}/owners/{owner}/$ref` | Partial: POST / DELETE | Local active user/SP references on static security groups; delegated role/ownership checks, atomic relationship history and audit facts. Last user owner cannot be removed. Existing v1.0 owner reads still omit SPs under the documented Microsoft staged-rollout profile. | [Add](https://learn.microsoft.com/en-us/graph/api/group-post-owners?view=graph-rest-1.0), [remove](https://learn.microsoft.com/en-us/graph/api/group-delete-owners?view=graph-rest-1.0) |
| `/v1.0/devices/{id}/registeredUsers`, `/v1.0/devices(deviceId='{key}')/registeredUsers` | Partial: GET | ID/type/displayName/UPN; unreadable fields null. `$select`, `$top`, durable paging; registered users and owners are distinct edges. Delegated reads require a supported directory role. | [Registered users](https://learn.microsoft.com/en-us/graph/api/device-list-registeredusers?view=graph-rest-1.0) |
| `/v1.0/devices/{id}/registeredUsers/$ref`, `/v1.0/devices/{id}/registeredUsers/{user}/$ref` | Partial: POST / DELETE | Delegated `Directory.AccessAsUser.All` plus Intune/Windows 365/Global Administrator; no application writes. POST also accepts deviceId addressing. Local active users only; delete removes the edge, never the user. | [Add](https://learn.microsoft.com/en-us/graph/api/device-post-registeredusers?view=graph-rest-1.0), [remove](https://learn.microsoft.com/en-us/graph/api/device-delete-registeredusers?view=graph-rest-1.0) |
| `/v1.0/users/{id}/appRoleAssignments`, `/v1.0/groups/{id}/appRoleAssignments`, `/v1.0/me/appRoleAssignments` | Partial: GET | Direct user/group assignments; user results include direct group memberships, not nested inheritance. Shares SP reverse view. Imported assignment evidence uses local GUID IDs and does not grant consent. Delegated role required; `/me` rejects app tokens. | [User](https://learn.microsoft.com/en-us/graph/api/user-list-approleassignments?view=graph-rest-1.0), [group](https://learn.microsoft.com/en-us/graph/api/group-list-approleassignments?view=graph-rest-1.0) |

Administrative fixture lifecycle: `GET`/`PUT /_mock/tenants/{tenant}/directory/collectionState`
exports/replaces organization fields, user/group app-role assignment evidence and device
registered-user references. Omitted sections stay unchanged; `{}` organization and empty
arrays clear the corresponding sections. Writes validate and commit atomically. Maximum
1000 assignments and 1000 registered-user references per import/export. Referenced tenant
objects must already exist. This is separate from directory NDJSON and tenant metadata
exports; it does not reset OAuth consent, credentials or roles. Deleting referenced users,
groups, devices, applications or service principals removes the corresponding fixture
relationships atomically. Explicit null assignment creation times remain null.

## Graph applications, service principals and devices

The [security contract](docs/security-contracts.md) specifies supported properties,
credential behavior and operation-specific authorization.

| Microsoft REST URI | MockAzure support | Beta | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- | --- |
| `/v1.0/applications`<br>`/v1.0/applications/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Object ID differs from app/client ID. Registration, credentials and grants share local OAuth state. Bounded properties/platforms. | [Application](https://learn.microsoft.com/en-us/graph/api/resources/application?view=graph-rest-1.0) |
| `/v1.0/servicePrincipals`<br>`/v1.0/servicePrincipals/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Canonical local principals/resource permission catalogs. Unsupported switches such as enabling `appRoleAssignmentRequired` are rejected. | [Service principal](https://learn.microsoft.com/en-us/graph/api/resources/serviceprincipal?view=graph-rest-1.0) |
| `/v1.0/devices`<br>`/v1.0/devices/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Stored inventory and bounded writes; creation requires delegated authority/role. No enrollment or compliance engine. Intune observation reads use separate deviceManagement routes below. | [Device](https://learn.microsoft.com/en-us/graph/api/resources/device?view=graph-rest-1.0) |
| `/v1.0/applications/{id}/owners`<br>`/v1.0/servicePrincipals/{id}/owners`<br>`/v1.0/devices/{id}/registeredOwners` | Implemented: GET collection; POST `/$ref`; DELETE `/{owner}/$ref` | Yes | Tenant-local references, transactional lifecycle cleanup and disclosure limits. Device owners are users. | [App owners](https://learn.microsoft.com/en-us/graph/api/application-list-owners?view=graph-rest-1.0), [SP owners](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-list-owners?view=graph-rest-1.0), [device owners](https://learn.microsoft.com/en-us/graph/api/device-list-registeredowners?view=graph-rest-1.0) |
| `/v1.0/applications/{id}/addPassword`<br>`/v1.0/servicePrincipals/{id}/addPassword` | Implemented: POST | Yes | One-time secret disclosure; subsequent token acquisition validates it. Not allowed for managed-identity backing registrations. | [App password](https://learn.microsoft.com/en-us/graph/api/application-addpassword?view=graph-rest-1.0), [SP password](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-addpassword?view=graph-rest-1.0) |
| `/v1.0/applications/{id}/removePassword`<br>`/v1.0/servicePrincipals/{id}/removePassword` | Implemented: POST | Yes | Revokes subsequent credential use, not issued token claims. Certificate replacement uses supported `keyCredentials` PATCH, not addKey/removeKey. | [Remove password](https://learn.microsoft.com/en-us/graph/api/application-removepassword?view=graph-rest-1.0) |

Alternate addressing works for these item routes and their listed child routes:
`/applications(appId='{appId}')`, `/servicePrincipals(appId='{appId}')`, and
`/devices(deviceId='{deviceId}')`. Password actions accept the `microsoft.graph.` namespace.
Public certificate bytes require explicit selection; private keys/secret verifiers are never returned.

## Graph permission grants and directory roles

| Microsoft REST URI | MockAzure support | Beta | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- | --- |
| `/v1.0/oauth2PermissionGrants`<br>`/v1.0/oauth2PermissionGrants/{id}` | Implemented: GET/POST collection; GET/PATCH/DELETE item | Yes | AllPrincipals/Principal consent; PATCH replaces scopes. Affects OAuth consent/refresh; permissions/roles enforced. Also `oAuth2PermissionGrants` spelling. | [Delegated grants](https://learn.microsoft.com/en-us/graph/api/resources/oauth2permissiongrant?view=graph-rest-1.0) |
| `/v1.0/servicePrincipals/{id}/oauth2PermissionGrants` | Implemented: GET | Yes | Scoped navigation; mutations use top-level routes. Supports appId addressing. | [SP grants](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-list-oauth2permissiongrants?view=graph-rest-1.0) |
| `/v1.0/servicePrincipals/{id}/appRoleAssignments`<br>`/v1.0/servicePrincipals/{id}/appRoleAssignedTo` | Implemented: GET/POST collection; GET/DELETE `/{assignment}` | Yes | Official permission IDs, distinct assignment IDs; affects new tokens, not issued claims. AppId addressing supported. v1.0 user/group navigation is listed in the directory expansion table above. Imported user/group assignments appear in reverse reads but remain fixture evidence; they do not grant OAuth permissions and cannot be changed through SP grant writes. | [Assignments](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-list-approleassignments?view=graph-rest-1.0), [assigned principals](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-list-approleassignedto?view=graph-rest-1.0) |
| `/v1.0/directoryRoles`<br>`/v1.0/directoryRoles/{id}`<br>`/v1.0/directoryRoles/{id}/members` | Partial: GET | Yes | Activated built-in roles/members and template addressing. No legacy activation/member-reference writes; use modern assignments. | [Directory roles](https://learn.microsoft.com/en-us/graph/api/resources/directoryrole?view=graph-rest-1.0) |
| `/v1.0/roleManagement/directory/roleDefinitions`<br>`/v1.0/roleManagement/directory/roleDefinitions/{id}` | Partial: GET | Yes | 27 built-ins include description labels and source-pinned `rolePermissions.allowedResourceActions`. Descriptions are local labels; action metadata is descriptive and does not add a custom-role evaluator. No custom-role CRUD or complete definition schema. | [Definitions](https://learn.microsoft.com/en-us/graph/api/rbacapplication-list-roledefinitions?view=graph-rest-1.0) |
| `/v1.0/roleManagement/directory/roleAssignments`<br>`/v1.0/roleManagement/directory/roleAssignments/{id}` | Partial: GET/POST collection; GET/DELETE item | Yes | Tenant scope `/`, user/SP principals; current roles affect authorization. Bounded principal/roleDefinition expansion. No PIM/custom/scoped roles. | [Assignments](https://learn.microsoft.com/en-us/graph/api/rbacapplication-list-roleassignments?view=graph-rest-1.0) |

Legacy role item/member reads also accept `/directoryRoles(roleTemplateId='{template}')`
and `/directoryRoles/roleTemplateId={template}`.

## Graph authentication methods, reports and policy

| Microsoft REST URI | MockAzure support | Beta | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- | --- |
| `/v1.0/policies/authenticationMethodsPolicy` | Partial: GET | No | Typed root; no root PATCH, campaigns or passwordless. Fresh tenants enforce; upgraded tenants activate on first configuration write. [Profile](integrations/contracts/authentication-policies.md). | [Policy](https://learn.microsoft.com/en-us/graph/api/authenticationmethodspolicy-get?view=graph-rest-1.0) |
| `/v1.0/policies/authenticationMethodsPolicy/authenticationMethodConfigurations`<br>`.../{Sms\|MicrosoftAuthenticator}` | Partial: GET collection/item, PATCH item | No | Enabled/disabled, group/all_users inclusion and group exclusion, mobile SMS or Authenticator push/any. Current registration required for simulated MFA. No advanced settings or query options. | [SMS](https://learn.microsoft.com/en-us/graph/api/smsauthenticationmethodconfiguration-update?view=graph-rest-1.0), [Authenticator](https://learn.microsoft.com/en-us/graph/api/microsoftauthenticatorauthenticationmethodconfiguration-update?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/methods` | Partial: GET collection and `/{method}` | Yes | Also `/me/authentication/methods`. Descriptor inspection; concrete write/reset routes below. No real challenges. No query options. | [Methods](https://learn.microsoft.com/en-us/graph/api/authentication-list-methods?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/passwordMethods` | Partial: GET collection and `/{method}` | Yes | Also `/me/...`. Reflects local password registration; password/createdDateTime null. Cloud-only delegated reset action below. | [Password](https://learn.microsoft.com/en-us/graph/api/authentication-list-passwordmethods?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/phoneMethods` | Partial: GET/POST collection; GET/PATCH/DELETE item | GET only | Fixed IDs; other-user POST/PATCH and authorized DELETE. `/me` supports GET/DELETE only; self creation/update is denied even through `/users/{self}`. Self deletion requires authentication within ten minutes. GET also supports beta. No SMS activation. | [Phone](https://learn.microsoft.com/en-us/graph/api/authentication-list-phonemethods?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/emailMethods` | Partial: GET/POST collection; GET/PATCH/DELETE item | GET only | Fixed recovery email ID; other-user POST/PATCH and authorized DELETE. `/me` supports GET/DELETE only; self creation/update is denied. Self deletion requires authentication within ten minutes. GET also supports beta. | [Email](https://learn.microsoft.com/en-us/graph/api/authentication-list-emailmethods?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/microsoftAuthenticatorMethods` | Partial: GET collection; GET/DELETE item | GET only | Also `/me/...` for GET/DELETE; self deletion requires authentication within ten minutes. v1.0 DELETE stored descriptor; no public push enrollment/delivery. | [Authenticator](https://learn.microsoft.com/en-us/graph/api/microsoftauthenticatorauthenticationmethod-list?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/fido2Methods` | Partial: GET collection; GET/DELETE item | GET only | Also `/me/...` for GET/DELETE; self deletion requires authentication within ten minutes. v1.0 DELETE stored descriptor; no public WebAuthn enrollment/attestation. Concrete app reads require declared passkey permission, not just generic methods scope. | [FIDO2](https://learn.microsoft.com/en-us/graph/api/fido2authenticationmethod-list?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/methods/{passwordId}/resetPassword`<br>`/v1.0/users/{user}/authentication/passwordMethods/{passwordId}/resetPassword` | Partial: POST | No | Also namespace-qualified `microsoft.graph.resetPassword`. Durable cloud-only delegated-admin reset; explicit/generated password, 202 and local Location, forced change. App/self/hybrid denied. [Profile](integrations/contracts/method-management.md). | [Reset](https://learn.microsoft.com/en-us/graph/api/authenticationmethod-resetpassword?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/operations/{id}` | Implemented: GET | No | Current delegated permission/role checks, target binding, $select, durable reset status; no credentials. | [Status](https://learn.microsoft.com/en-us/graph/api/longrunningoperation-get?view=graph-rest-1.0) |
| `/v1.0/reports/authenticationMethods/userRegistrationDetails`<br>`/v1.0/reports/authenticationMethods/userRegistrationDetails/{id}` | Partial: GET | Yes | Derived from persisted registrations. AuditLog.Read.All, P1/P2 and delegated reader roles. Filter/continuation only; no select/top. No real SSPR service. | [Registration](https://learn.microsoft.com/en-us/graph/api/authenticationmethodsroot-list-userregistrationdetails?view=graph-rest-1.0) |
| `/v1.0/users?$select=id,signInActivity`<br>`/v1.0/users/{user}?$select=signInActivity` | Partial: GET | Yes | Also selected on `/me`. Last interactive/noninteractive attempts/success survives raw evidence retention. User access + AuditLog.Read.All, P1/P2 and applicable role. No arbitrary signInActivity filtering. | [Sign-in activity](https://learn.microsoft.com/en-us/graph/api/resources/signinactivity?view=graph-rest-1.0) |
| `/v1.0/identity/conditionalAccess/policies`<br>`/v1.0/identity/conditionalAccess/policies/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Existing subset | Existing user/group/app/IP conditions plus v1.0 built-in role and simulated-platform targets; block, MFA and compliantDevice with AND/OR. Trusted admin device binding and current Intune compliance required. Report-only evidence. No risk, advanced device filters, session or strength controls. [Profile](integrations/contracts/authentication-policies.md). | [Policies](https://learn.microsoft.com/en-us/graph/api/conditionalaccessroot-list-policies?view=graph-rest-1.0) |
| `/v1.0/identity/conditionalAccess/namedLocations`<br>`/v1.0/identity/conditionalAccess/namedLocations/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | IPv4/IPv6 IP locations; actual connection peer used. No country locations. Referenced locations cannot be deleted. | [Locations](https://learn.microsoft.com/en-us/graph/api/conditionalaccessroot-list-namedlocations?view=graph-rest-1.0) |

Phone/email methods support the Graph writes listed above. Authenticator/FIDO2 descriptors
are enrolled through emulator administration; Graph can inspect/delete them, not perform
real push or hardware enrollment.
Unsupported nonempty CA controls are rejected even on disabled/report-only policies.
See [security contracts](docs/security-contracts.md).

## Graph sign-ins, audits, risk and licensing

Evidence APIs read persisted imports and supported native activity projections.
Availability delay, retention, tenant boundaries and disclosure checks apply; a new
installation is not automatically populated with historical records.

| Microsoft REST URI | MockAzure support | Beta | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- | --- |
| `/v1.0/auditLogs/signIns`<br>`/v1.0/auditLogs/signIns/{id}` | Partial: GET | Yes | v1.0 interactive-user profile; beta supports explicit event-type filters for other captured flows. Tenant reads need AuditLog.Read.All and applicable role; eligible delegated self-only branch exists. P1/P2 required; risk/CA details have extra disclosure gates. | [Sign-ins](https://learn.microsoft.com/en-us/graph/api/signin-list?view=graph-rest-1.0) |
| `/v1.0/auditLogs/directoryAudits`<br>`/v1.0/auditLogs/directoryAudits/{id}` | Implemented: GET | Yes | Imported/native immutable facts. AuditLog.Read.All or Directory.Read.All plus applicable delegated role. Free/P1/P2; declared filter/order subset. | [Audits](https://learn.microsoft.com/en-us/graph/api/directoryaudit-list?view=graph-rest-1.0) |
| `/v1.0/identityProtection/riskyUsers`<br>`/v1.0/identityProtection/riskyUsers/{id}` | Partial: GET | Yes | Persisted risk state, not proprietary scoring. IdentityRiskyUser.Read.All, applicable delegated role and P2. | [Risky users](https://learn.microsoft.com/en-us/graph/api/riskyuser-list?view=graph-rest-1.0) |
| `/v1.0/identityProtection/riskDetections`<br>`/v1.0/identityProtection/riskDetections/{id}` | Partial: GET | Yes | Imported/local observations. IdentityRiskEvent.Read.All and role; P1/P2 with tier-dependent redaction. No threat-detection engine. | [Detections](https://learn.microsoft.com/en-us/graph/api/riskdetection-list?view=graph-rest-1.0) |
| `/v1.0/identityProtection/riskyUsers/confirmCompromised`<br>`/v1.0/identityProtection/riskyUsers/dismiss` | Implemented: POST | No | Bounded userIds list; updates risk state/evidence. IdentityRiskyUser.ReadWrite.All, delegated security-admin authority and P2. Namespaced action aliases also work. | [Confirm](https://learn.microsoft.com/en-us/graph/api/riskyuser-confirmcompromised?view=graph-rest-1.0), [dismiss](https://learn.microsoft.com/en-us/graph/api/riskyuser-dismiss?view=graph-rest-1.0) |
| `/v1.0/subscribedSkus` | Partial: GET | No | Explicitly provisioned SKU/plan inventory, not the full product catalog. No item route or Graph SKU writes. | [SKUs](https://learn.microsoft.com/en-us/graph/api/subscribedsku-list?view=graph-rest-1.0) |
| `/v1.0/users/{user}/licenseDetails`<br>`/v1.0/me/licenseDetails` | Partial: GET | No | Delegated only in this implementation; current assignments and supported scopes/roles. No application-authenticated read. | [License details](https://learn.microsoft.com/en-us/graph/api/user-list-licensedetails?view=graph-rest-1.0) |
| `/v1.0/users/{user}/assignLicense` | Implemented: POST | No | Usage location, inventory/capacity validation; transactional assignment/removal/audit. Also microsoft.graph.assignLicense. No group-based licensing engine. | [Assign](https://learn.microsoft.com/en-us/graph/api/user-assignlicense?view=graph-rest-1.0) |

The [evidence contract](integrations/contracts/graph-evidence.md) contains exact permission
alternatives, delegated role matrices and P1/P2 redaction behavior.

## Graph security collections

All five families require typed **admin imports** before they contain evidence. Public
Graph methods read stored resources; they do not execute detection, remediation or score
calculation. See [collection evidence](docs/collection-evidence.md) for import limits,
filter fields and snapshot guarantees.

| Microsoft REST URI | MockAzure support | Beta | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- | --- |
| `/v1.0/security/alerts`<br>`/v1.0/security/alerts/{id}` | Historical: GET | No | Legacy shape; SecurityEvents.Read.All or ReadWrite. Retained for old collectors. Microsoft marks it deprecated, with retirement scheduled October 15, 2026; local availability is not an upstream availability claim. No PATCH. | [Legacy alerts](https://learn.microsoft.com/en-us/graph/api/alert-list?view=graph-rest-1.0) |
| `/v1.0/security/alerts_v2`<br>`/v1.0/security/alerts_v2/{id}` | Partial: GET | No | SecurityAlert.Read.All or ReadWrite; delegated reader/security role. Typed derived evidence. SecurityEvents.Read.All alone is insufficient. No PATCH/remediation. | [Modern alerts](https://learn.microsoft.com/en-us/graph/api/security-list-alerts_v2?view=graph-rest-1.0) |
| `/v1.0/security/incidents`<br>`/v1.0/security/incidents/{id}` | Partial: GET | No | SecurityIncident.Read.All or ReadWrite; delegated role. Expand alerts joins imported modern alerts by incidentId at the same snapshot. No PATCH/comments/actions. | [Incidents](https://learn.microsoft.com/en-us/graph/api/security-list-incidents?view=graph-rest-1.0) |
| `/v1.0/security/secureScores`<br>`/v1.0/security/secureScores/{id}` | Partial: GET | No | SecurityEvents.Read.All or ReadWrite. Imported score history, not recomputed from tenant/policy mutations. | [Scores](https://learn.microsoft.com/en-us/graph/api/security-list-securescores?view=graph-rest-1.0) |
| `/v1.0/security/secureScoreControlProfiles`<br>`/v1.0/security/secureScoreControlProfiles/{id}` | Partial: GET | No | SecurityEvents.Read.All or ReadWrite. Imported controls; no public updates or automatic recommendations. | [Profiles](https://learn.microsoft.com/en-us/graph/api/security-list-securescorecontrolprofiles?view=graph-rest-1.0) |

## Graph mail and Unified Audit Log

| Microsoft REST URI | MockAzure support | Beta | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- | --- |
| `/v1.0/users/{user}/mailFolders/inbox/messageRules`<br>`/v1.0/users/{user}/mailFolders/inbox/messageRules/{rule}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Also `/me/mailFolders/inbox/messageRules`. Shared Exchange rule state. MailboxSettings.Read/ReadWrite and mailbox access/entitlement checks. Inbox only; hidden rules omitted, read-only writes rejected. Rules stored, not executed on messages. | [Rules](https://learn.microsoft.com/en-us/graph/api/mailfolder-list-messagerules?view=graph-rest-1.0) |
| `/v1.0/users/{user}/mailFolders[/{folder}]`<br>`/v1.0/users/{user}/mailFolders/{folder}/childFolders` | Partial: GET | No | Primary folder hierarchy, hidden-folder option, selection and durable paging; `/me` aliases. Legacy statistics remain explicit; content mode derives counts. | [Folders](https://learn.microsoft.com/en-us/graph/api/user-list-mailfolders?view=graph-rest-1.0) |
| `/v1.0/users/{user}/messages[/{message}]`<br>`/v1.0/users/{user}/mailFolders/{folder}/messages[/{message}]` | Partial: GET | No | Typed imported metadata, bodies and selected headers; Mail.ReadBasic restrictions; delegated own-mailbox scopes or Mail.Read.Shared/ReadWrite.Shared plus current operational FullAccess for another mailbox. Shared basic scope cannot disclose bodies. Selection, received-time/subject/isRead filters, bounded ordering, count and snapshot pages. `/me` aliases; no sending. | [Messages](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0) |
| `/v1.0/users/{user}/messages/{message}/attachments[/{attachment}]`<br>`/v1.0/users/{user}/messages/{message}/$value`<br>`/v1.0/users/{user}/messages/{message}/attachments/{attachment}/$value` | Partial: GET | No | File/item/reference distinctions; exact stored MIME/file bytes; one-level message item expansion. Reference raw content returns405. JSON contentBytes above4MiB requires metadata selection plus streaming raw read; attachment pages have an8MiB aggregate cap and continuation. Folder-scoped and `/me` aliases. | [Attachments](https://learn.microsoft.com/en-us/graph/api/attachment-get?view=graph-rest-1.0) |
| `/v1.0/users/{user}/mailboxSettings` | Partial: GET | No | Persisted official settings fields and selection; distinct MailboxSettings.Read/ReadWrite check; `/me` alias. No settings writes. | [Settings](https://learn.microsoft.com/en-us/graph/api/user-get-mailboxsettings?view=graph-rest-1.0) |
| `/v1.0/users/{user}/messages/{message}`<br>`/v1.0/users/{user}/messages/{message}/move`<br>`/v1.0/users/{user}/messages/{message}/copy` | Partial: PATCH/DELETE item; POST move/copy | No | PATCH isRead/categories only; Mail.ReadWrite or delegated Mail.ReadWrite.Shared with current FullAccess for another mailbox. Folder-scoped and `/me` aliases. Move changes normal ID, preserves immutable ID; copy creates independent identity. Native item audit facts; no delivery or recoverable-items service. | [Update](https://learn.microsoft.com/en-us/graph/api/message-update?view=graph-rest-1.0), [Move](https://learn.microsoft.com/en-us/graph/api/message-move?view=graph-rest-1.0) |
| `/v1.0/users/{user}/mailFolders/{folder}/messages/delta` | Partial: GET | No | `/me` alias. Persistent folder snapshots and final delta links; one-hour expiry, reauthorization, move/delete removals. Select/top, receivedDateTime ge/gt, receivedDateTime desc ordering. No changeType or expand. | [Delta](https://learn.microsoft.com/en-us/graph/api/message-delta?view=graph-rest-1.0) |
| `/v1.0/security/auditLog/queries`<br>`/v1.0/security/auditLog/queries/{query}` | Implemented: GET/POST collection; GET item | Yes | Durable asynchronous search; worker required. AuditLogsQuery.Read.All or supported workload scopes, Audit Standard/Premium, separate Purview role for delegated callers. No update/delete/cancel. Beta uses bounded v1.0 profile. | [Queries](https://learn.microsoft.com/en-us/graph/api/security-auditcoreroot-list-auditlogqueries?view=graph-rest-1.0) |
| `/v1.0/security/auditLog/queries/{query}/records` | Implemented: GET | Yes | Retained results, stable paging/current authorization. Seven-day query lifetime, one-hour page handles. Imports and native mail-configuration/message-mutation audits. No direct `/security/auditLog/records` collection. | [Records](https://learn.microsoft.com/en-us/graph/api/security-auditlogquery-list-records?view=graph-rest-1.0) |

UAL filters cover time, workload, operation, type, UPN, IP, object, administrative unit
and bounded literal keywords. SharePoint/OneDrive content APIs have a separate imported, versioned evidence store
(see the workload table below). See [mail contracts](docs/mail-contracts.md).

## Graph query and protocol coverage

Query support is **per endpoint**, not a global OData promise. A supported route can
correctly reject an unsupported query or property.

| Surface / feature | Implemented behavior | Important limits |
| --- | --- | --- |
| Users/groups | Declared filter/select/orderby/top/count, opaque nextLink; group expand=members with inner selection | Default 100, cap 999/page; members expansion contains at most 20 entries without nested nextLink; use the members collection for complete results. Advanced combinations require ConsistencyLevel: eventual and applicable count. No search/skip or user expansion. |
| Apps/SPs/devices, grants, roles, CA | Typed filter/selection, SQL snapshots/paging; specific owner/principal/role-definition expansions | Property/operator allowlists differ. No arbitrary expansion/search. See [query contracts](src/mockazure/providers/graph/security/queries.py). |
| Sign-ins/directory audits | Filter, top, timestamp ordering, nextLink | Directory-audit ordering is documented; sign-in ordering is a retained local extension. No select/count/expand/skip/search. Page cap 1000. |
| Risky users/risk detections | Filter, select, top, nextLink | No orderby/count/expand/skip; cap 500. |
| Registration report | Declared filter, server continuation | No client top/select/order/expand. Method inventory accepts no optional query parameters. |
| Security collections | Filter/top/skip/count/nextLink; legacy alerts add select and one scalar orderby; incidents add expand=alerts | Default 100, cap 1000; top is page size. Legacy skip cap 500, others 100,000; expansion cap 10,000. Modern alerts/scores/profiles do not inherit legacy select/order support. |
| Inbox rules | Select/top, snapshot nextLink | Max 1000 rules/mailbox; page cap 100, accepted top 1–1000. No filter/order/expand. |
| UAL query lists/results | Select/top/nextLink; search filters in POST body | Page cap 100, accepted top 1–1000. Item query supports selection only. |
| Directory delta | Users/groups; select and issued state links | No arbitrary filter/top/order/expand. Mail-folder **message** delta and drive-root delta have separate profiles below. |
| `POST /v1.0/$batch` (also beta) | 1–20 independent GETs; per-item authorization/errors | Allowlisted roots include directoryObjects, apps/SPs/devices, roles/grants, identity/conditionalAccess, reports, security, sites/drives, teams/chats and deviceManagement, plus users/groups/me and evidence roots. Unsupported inner operations still fail. The top-level `policies` root (authenticationMethodsPolicy) is **not** batch-enabled. No writes/dependencies, percent-encoded paths or external URLs; 8 MiB/item and 32 MiB aggregate response limits; redirects are not followed. [Batching](https://learn.microsoft.com/en-us/graph/json-batching). |
| Throttling | Shared PostgreSQL budgets and one-shot read/page 429 for directory/evidence, app/device, role/grant/CA/report, security collections, sites/drives, teams/chats and deviceManagement; Exchange trace rolling window; ARM Activity faults; separate Activity feed publisher quota | Retry-After; scope is tenant/client/resource and shared across API instances. Ordinary `/me` and mail aliases share the user bucket; joinedTeams/chats/managedDevices aliases use their workload buckets. Method-policy routes are not a declared budget bucket. Batch charges inner calls only. Local quotas are configurable, not a promise to reproduce every Microsoft quota. |
| Mail content | Message select/filter/orderby/top/count, folder/attachment select/top, folder-message delta | Default top 10, accepted 1–1000; Prefer page size can lower it. Ordered fields must lead the message filter. Only itemAttachment/item expansion; raw content accepts no query. See [mail profile](docs/mail-contracts.md). |
| SharePoint/OneDrive | Select/top (1–1000), bounded order, site `search`, list-item fields expansion; root delta | No generic filter/count/search. `search` is unprefixed and required on `/sites`; ordering is a local extension where undocumented upstream. Delta accepts select/top/token, not order/expand. [Profile](integrations/contracts/site-drive.md). |
| Teams/chats | Operation-specific selection, equality/date filters, ordering, replies expansion and paging | Message top 1–50; team/channel member top 1–999; joinedTeams, chat item/members and message item/raw reads have no optional query. Recognized chat date filters without matching explicit order are ignored. [Profile](integrations/contracts/teams.md). |
| Intune | Collection select/top (1–1000), one allowed-field string equality filter, one allowed-field order; item select | No count/expand/search/delta; one-hour stored continuations. Assignment/status evidence does not calculate device compliance. [Profile](integrations/contracts/intune.md). |
| Authentication-method policy | Root/configuration GET, configuration PATCH | No query options, beta routes or batch root. Method inventory and reset-operation status have their own query profiles above. |
| Continuations | Local origins; tenant/caller/query binding; reauthorization | Family-specific lifetime/snapshot behavior. Use nextLink unchanged. An odata.context value does not implement GET /$metadata. |
| Errors/authentication | Local token signature/audience/expiry/current identity checks; Graph-shaped errors | Missing provider route normally 501 MockAzureOperationNotImplemented; unsupported method 405; unsupported query usually 400. Missing objects/denied access are not coverage gaps. |

Query names above refer to OData parameters prefixed with `$`, such as `$select`.

## Graph SharePoint and OneDrive

These adapters are **v1.0 only**. Imported typed evidence and explicit access grants are
required. Read-only paths do not imply Microsoft provisioning, upload, sharing or sync services.

| Microsoft REST URI | MockAzure support | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- |
| `/v1.0/sites/{site}`, `/v1.0/sites/root`, `/v1.0/sites/{hostname}:/{path}` | Partial: GET | Composite IDs, one imported root, hostname/path lookup against imported webUrl; URLs are never fetched. | [Site](https://learn.microsoft.com/en-us/graph/api/site-get?view=graph-rest-1.0) |
| `/v1.0/sites?search={term}` | Partial: GET | Literal case-insensitive name substring, or `*`; search parameter required. Sites.Selected cannot enumerate/search. Delegated results require user site ACLs. | [Search](https://learn.microsoft.com/en-us/graph/api/site-search?view=graph-rest-1.0) |
| `/v1.0/sites/{site}/lists[/{list}]` | Partial: GET | List metadata, selection/order/pages; no creation or schema writes. | [Lists](https://learn.microsoft.com/en-us/graph/api/list-list?view=graph-rest-1.0) |
| `/v1.0/sites/{site}/lists/{list}/items[/{item}]` | Partial: GET | Stored items; fields or fields($select=...) expansion; no general filter or public item writes. | [Items](https://learn.microsoft.com/en-us/graph/api/listitem-list?view=graph-rest-1.0) |
| `/v1.0/sites/{site}/lists/{list}/items/{item}/fields` | Partial: GET | Open fieldValueSet, optional field selection; no field writes. | [Fields](https://learn.microsoft.com/en-us/graph/api/resources/fieldvalueset?view=graph-rest-1.0) |
| `/v1.0/sites/{site}/drives[/{drive}]`, `/v1.0/drives/{drive}` | Partial: GET | Canonical imported drive metadata; site child ID must belong to that site. | [Drives](https://learn.microsoft.com/en-us/graph/api/drive-list?view=graph-rest-1.0) |
| `/v1.0/users/{id}/drives`, `/v1.0/groups/{id}/drives`, `/v1.0/me/drives` | Partial: GET | Explicit directory-to-drive edges; user/group IDs must be UUIDs, not UPNs. `/me` is delegated only. | [Drives](https://learn.microsoft.com/en-us/graph/api/drive-list?view=graph-rest-1.0) |
| `/v1.0/users/{id}/drive`, `/v1.0/groups/{id}/drive`, `/v1.0/me/drive`, `/v1.0/sites/{site}/drive` | Partial: GET | Exactly one imported default drive required. Supports the canonical item/content/delta tails listed below; no implicit provisioning. | [Drive](https://learn.microsoft.com/en-us/graph/api/drive-get?view=graph-rest-1.0) |
| `/v1.0/drives/{drive}/root`, `/v1.0/drives/{drive}/items/{item}`, `/v1.0/drives/{drive}/root:/{path}` | Partial: GET | Stored tree and root-relative path addressing; imported parent/count metadata; no public editing. Item-relative path addressing unsupported. | [Item](https://learn.microsoft.com/en-us/graph/api/driveitem-get?view=graph-rest-1.0) |
| `/v1.0/drives/{drive}/items/{item}/children`, `/v1.0/drives/{drive}/root/children` | Partial: GET | Folder children, snapshot paging; no child creation. | [Children](https://learn.microsoft.com/en-us/graph/api/driveitem-list-children?view=graph-rest-1.0) |
| `/v1.0/drives/{drive}/items/{item}/permissions[/{permission}]` | Partial: GET | Imported display evidence; these rows do not confer access. No sharing writes. | [Permissions](https://learn.microsoft.com/en-us/graph/api/driveitem-list-permissions?view=graph-rest-1.0) |
| `/v1.0/drives/{drive}/items/{item}/versions[/{version}]` | Partial: GET | Imported version metadata; no version restore/delete. | [Versions](https://learn.microsoft.com/en-us/graph/api/driveitem-list-versions?view=graph-rest-1.0) |
| `/v1.0/drives/{drive}/items/{item}/content`, `/v1.0/drives/{drive}/items/{item}/versions/{version}/content` | Partial: GET | Exact stored bytes through five-minute local capability redirect, Range and ETag. Issued URLs survive ACL revocation until expiry; no remote fetch/upload. | [Content](https://learn.microsoft.com/en-us/graph/api/driveitem-get-content?view=graph-rest-1.0) |
| `/v1.0/drives/{drive}/root/delta`, `/v1.0/drives/{drive}/items/{root-id}/delta` | Partial: GET | Durable changes/removals, latest checkpoint, function tokens; root only, driven by metadata imports. Not arbitrary-folder delta. | [Delta](https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0) |
| `/v1.0/sites/{site}/permissions`<br>`/v1.0/sites/{site}/permissions/{permission}` | Partial: GET/POST collection; GET/DELETE item | Authoritative selected-app grants. Sites.FullControl.All; delegated calls also require supported role and user ACL. Imported display grants excluded; no PATCH. Native facts have no established UAL/feed mapping. | [Site permissions](https://learn.microsoft.com/en-us/graph/api/site-post-permissions?view=graph-rest-1.0) |

The [site/drive contract](integrations/contracts/site-drive.md) defines permission supersets,
selected-app grants, delegated ACLs, query extensions and complete import/export behavior.

## Graph Teams and chats

These adapters are **v1.0 only**, with explicit memberships and operation-specific tenant-wide
or resource-specific application permissions. Imported member rows do not grant access.

| Microsoft REST URI | MockAzure support | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- |
| `/v1.0/teams/{team}` | Partial: GET | Imported team; selection; no public team writes or membership bypass for administrators. | [Team](https://learn.microsoft.com/en-us/graph/api/team-get?view=graph-rest-1.0) |
| `/v1.0/me/joinedTeams`, `/v1.0/users/{user}/joinedTeams` | Partial: GET | Explicit membership discovery; user ID/UPN; delegated user aliases are self-only. No optional query. | [Joined teams](https://learn.microsoft.com/en-us/graph/api/user-list-joinedteams?view=graph-rest-1.0) |
| `/v1.0/teams/{team}/channels[/{channel}]` | Partial: GET | Private/shared channel visibility follows explicit membership; bounded selection/equality filter for collections. | [Channels](https://learn.microsoft.com/en-us/graph/api/channel-list?view=graph-rest-1.0) |
| `/v1.0/teams/{team}/members`, `/v1.0/teams/{team}/channels/{channel}/members` | Partial: GET collection | Imported member evidence; selection/top/equality filters. Individual `/members/{id}` and public membership writes are unsupported. | [Members](https://learn.microsoft.com/en-us/graph/api/team-list-members?view=graph-rest-1.0) |
| `/v1.0/teams/{team}/channels/{channel}/messages[/{message}]` | Partial: GET | Durable pages and observed edits/deletions; replies expansion with nested continuation. No send/edit/delete/delta operations. | [Messages](https://learn.microsoft.com/en-us/graph/api/channel-list-messages?view=graph-rest-1.0) |
| `/v1.0/teams/{team}/channels/{channel}/messages/{message}/replies[/{reply}]` | Partial: GET | Stored replies with paging; no reply writes. | [Replies](https://learn.microsoft.com/en-us/graph/api/chatmessage-list-replies?view=graph-rest-1.0) |
| `/v1.0/chats`, `/v1.0/me/chats`, `/v1.0/users/{user}/chats` | Partial: GET | Self delegated discovery; application discovery requires the user-scoped route. Bounded chat filters/order/pages; no tenant-wide application `/chats` discovery. | [Chats](https://learn.microsoft.com/en-us/graph/api/chat-list?view=graph-rest-1.0) |
| `/v1.0/chats/{chat}`, `/v1.0/chats/{chat}/members` | Partial: GET | Chat item and member collection; no optional query, individual member reads or public writes. User-scoped and `/me` chat aliases use the same authority. | [Chat](https://learn.microsoft.com/en-us/graph/api/chat-get?view=graph-rest-1.0) |
| `/v1.0/chats/{chat}/messages[/{message}]` | Partial: GET | Date/order-specific collection queries; message item reads have no query options. RSC collection authority does not automatically authorize item or hosted-content reads. | [Chat messages](https://learn.microsoft.com/en-us/graph/api/chat-list-messages?view=graph-rest-1.0) |
| `/v1.0/chats/{chat}/messages/{message}/hostedContents[/{content}][/$value]`<br>`/v1.0/teams/{team}/channels/{channel}/messages/{message}/hostedContents[/{content}][/$value]` | Partial: GET | Collection/item metadata; `/$value` requires a content ID and returns exact local bytes. Same hostedContents suffix on `/messages/{message}/replies/{reply}`. Attachment URLs are never fetched. | [Hosted content](https://learn.microsoft.com/en-us/graph/api/chatmessagehostedcontent-get?view=graph-rest-1.0) |
| `/v1.0/teams/{team}/channels/{channel}/filesFolder` | Partial: GET | Canonical imported SharePoint folder; operation-specific file authority and delegated site ACL. No provisioning. | [Files folder](https://learn.microsoft.com/en-us/graph/api/channel-get-filesfolder?view=graph-rest-1.0) |

The [Teams contract](integrations/contracts/teams.md) records all query, membership,
RSC and client boundaries. Calls, meetings, federation and installed-app semantics remain unsupported.

## Graph Intune

These adapters are **v1.0 only**. An active tenant Intune plan and current operation-specific
Graph permissions are required. Imported policy/status evidence does not execute device management.

| Microsoft REST URI | MockAzure support | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- |
| `/v1.0/deviceManagement/managedDevices[/{id}]`<br>`/v1.0/users/{user}/managedDevices[/{id}]`, `/v1.0/me/managedDevices[/{id}]` | Partial: GET | Licensed typed inventory, explicit user relationships; ID is the Intune record ID, azureADDeviceId maps to directory deviceId. Observed compliance only. | [Devices](https://learn.microsoft.com/en-us/graph/api/intune-devices-manageddevice-list?view=graph-rest-1.0) |
| `/v1.0/deviceManagement/detectedApps[/{id}]`<br>`/v1.0/deviceManagement/detectedApps/{id}/managedDevices[/{device}]` | Partial: GET | Imported applications and explicit device relationships; inventory permission family. | [Apps](https://learn.microsoft.com/en-us/graph/api/intune-devices-detectedapp-list?view=graph-rest-1.0) |
| `/v1.0/deviceManagement/deviceConfigurations[/{id}]`<br>`/v1.0/deviceManagement/deviceCompliancePolicies[/{id}]` | Partial: GET | Windows10, iOS, Android and macOS general configuration/compliance types; separate configuration permission. No settings catalog or other derived types. | [Configuration](https://learn.microsoft.com/en-us/graph/api/intune-deviceconfig-deviceconfiguration-list?view=graph-rest-1.0) |
| `/v1.0/deviceManagement/deviceConfigurations/{id}/assignments[/{assignment}]`<br>`/v1.0/deviceManagement/deviceCompliancePolicies/{id}/assignments[/{assignment}]` | Partial: GET | Explicit typed assignment intent, item reads and paging; assignments never infer device compliance. | [Assignments](https://learn.microsoft.com/en-us/graph/api/intune-deviceconfig-devicecompliancepolicyassignment-list?view=graph-rest-1.0) |
| `/v1.0/deviceManagement/deviceConfigurations/{id}/deviceStatuses[/{status}]`<br>`/v1.0/deviceManagement/deviceCompliancePolicies/{id}/deviceStatuses[/{status}]` | Partial: GET | Observed statuses with full paging; no on-device evaluation. | [Statuses](https://learn.microsoft.com/en-us/graph/api/intune-deviceconfig-devicecompliancedevicestatus-list?view=graph-rest-1.0) |

The [Intune contract](integrations/contracts/intune.md) specifies typed imports, permissions,
query fields and the trusted observed-compliance contract used by Conditional Access.

## Graph gaps

These specific operations remain unsupported. Coverage in a related collection or service
must not be interpreted as support for these paths.

| Microsoft REST URI / operation | Status | Behavior and remaining limits | Microsoft reference |
| --- | --- | --- | --- |
| `/v1.0/users/{user}/sendMail` | Unsupported | Mail sending/draft composition, calendar and contact APIs remain outside this program. | [Send mail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0) |
| `/v1.0/directory/deletedItems` | Unsupported | Generic deleted-item reads/restore remain absent. | [Deleted items](https://learn.microsoft.com/en-us/graph/api/directory-deleteditems-list?view=graph-rest-1.0) |
| `/v1.0/applications/{id}/addKey`, `/v1.0/applications/{id}/removeKey` (also servicePrincipals) | Unsupported | Proof-of-possession actions; supported credential PATCH/admin provisioning is separate. | [Add key](https://learn.microsoft.com/en-us/graph/api/application-addkey?view=graph-rest-1.0) |
| `/v1.0/roleManagement/directory/roleEligibilityScheduleRequests`, `/v1.0/roleManagement/directory/roleAssignmentScheduleRequests` | Unsupported | PIM schedules/eligibility/activation; no custom/scoped role lifecycle. | [PIM](https://learn.microsoft.com/en-us/graph/api/resources/privilegedidentitymanagementv3-overview?view=graph-rest-1.0) |
| `/v1.0/communications/calls`, `/v1.0/users/{user}/events`, `/v1.0/users/{user}/contacts` | Unsupported | Calls/meetings, calendars and contacts. | [Teams](https://learn.microsoft.com/en-us/graph/api/resources/teams-api-overview?view=graph-rest-1.0), [calendars](https://learn.microsoft.com/en-us/graph/outlook-calendar-concept-overview) |
| Intune enrollment, device actions, public policy writes, scope tags/RBAC, settings catalog, delta and beta | Unsupported | No wipe/retire/check-in or on-device enforcement. Delegated reads use broad Graph authority; Intune role scope restrictions are not simulated. | [Intune overview](https://learn.microsoft.com/en-us/graph/api/resources/intune-graph-overview?view=graph-rest-1.0) |
| `/v1.0/security/runHuntingQuery` | Unsupported | KQL/advanced hunting; imported alerts/incidents are not a hunting engine. | [Hunting](https://learn.microsoft.com/en-us/graph/api/security-security-runhuntingquery?view=graph-rest-1.0) |
| `/v1.0/security/cases/ediscoveryCases`, `/v1.0/security/labels/retentionLabels` | Unsupported | eDiscovery/retention operations; UAL search is the supported Purview-related surface. | [eDiscovery](https://learn.microsoft.com/en-us/graph/api/resources/security-ediscoverycase?view=graph-rest-1.0), [labels](https://learn.microsoft.com/en-us/graph/api/resources/security-retentionlabel?view=graph-rest-1.0) |
| `/v1.0/subscriptions`, `/v1.0/$metadata`; delta except users/groups, mail-folder messages and drive roots | Unsupported | Graph notifications/webhooks, public metadata serving, extra delta APIs. ARM subscriptions are unrelated. | [Subscriptions](https://learn.microsoft.com/en-us/graph/api/resources/subscription?view=graph-rest-1.0), [metadata](https://learn.microsoft.com/en-us/graph/traverse-the-graph) |

## Entra authentication

The local issuer validates registered secrets/certificate assertions and signs tokens.
Permissions, consent, roles, current identities and supported Conditional Access policies
affect real local requests. See the [identity guide](docs/identity.md).

| Microsoft protocol URI / operation | MockAzure support | Limits / notes | Microsoft reference |
| --- | --- | --- | --- |
| `GET /common/discovery/instance` | Implemented | Local discovery aliases; never discovers/contacts real tenants. | [Discovery](https://learn.microsoft.com/en-us/entra/identity-platform/msal-client-application-configuration) |
| `GET /{tenant}/v2.0/.well-known/openid-configuration`<br>`GET /{tenant}/discovery/v2.0/keys` | Implemented | Local metadata/JWKS; persistent signing keys/rotation. | [OIDC](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc) |
| `POST /{tenant}/oauth2/v2.0/token` | Partial | Client credentials (secret/certificate), authorization-code/device-code redemption, refresh. Local registered clients; no ROPC, implicit or on-behalf-of grant. | [Client credentials](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow), [code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow) |
| `GET /{tenant}/oauth2/v2.0/authorize` | Partial | Code flow with PKCE, local login/consent/simulated MFA outcome. No external IdP federation. | [Code flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow) |
| `POST /{tenant}/oauth2/v2.0/devicecode` | Implemented | Local approval/polling/persistent sessions; verification UI at `/device`. | [Device flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-device-code) |
| Host `GET {IDENTITY_ENDPOINT}?api-version=2019-08-01&resource=...` | Partial | Local `/MSI/token`, host credential header, attached system/user identities and supported selectors. No IMDS link-local binding/full Azure host environment. | [Managed identity](https://learn.microsoft.com/en-us/azure/app-service/overview-managed-identity) |
| `/{tenant}/adminconsent`, `/{tenant}/v2.0/adminconsent`, v1 `/oauth2/token`, federation/logout endpoints | Unsupported | Consent through supported local flow/admin grants. Legacy fixture routes do not imply current support. | [Admin consent](https://learn.microsoft.com/en-us/entra/identity-platform/v2-admin-consent) |

POST `/login`, `/consent`, `/mfa` and GET `/device` are local browser-flow handlers,
not reproductions of Microsoft's private login-page endpoints.

## Exchange Online

The supported integration is the original **ExchangeOnlineManagement 3.11.0** module
plus bounded server-generated proxies making real local requests. These transport routes
are **observed module behavior**, not documented public Graph REST APIs. Public command
semantics are linked to Microsoft PowerShell references.

| Service transport | MockAzure support | Notes |
| --- | --- | --- |
| `GET /AdminApi/v1.0/{tenant}/EXOModuleFile?Version=3.11.0` | Partial | Downloads implemented, currently authorized command proxies. Other module versions not qualified. |
| `POST /adminapi/v2.0/{tenant}/InvokeCommand`<br>`POST /adminapi/beta/{tenant}/InvokeCommand` | Partial | Fixed command allowlist below; validates parameters, entitlements, current role/grant and mailbox scope. |
| `GET /adminapi/beta/{tenant}/Mailbox('{key}')` | Partial | Compiled Get-EXOMailbox; base64 UTF-8 identity and isEncoded=true. |
| `GET /adminapi/beta/{tenant}/Recipient`<br>`GET /adminapi/beta/{tenant}/Recipient('{key}')` | Partial | Original Get-EXORecipient, encoded identity/header selectors; current recipient scope, SQL-filtered pages and local nextLink. Minimum/explicit supported properties only. |
| `GET /adminapi/beta/{tenant}/Mailbox('{key}')/MailboxPermission` | Partial | Original Get-EXOMailboxPermission identity read. Pinned PermissionId/PermissionList DTO; the module flattens each ACE. |
| `GET /adminapi/beta/{tenant}/Recipient('{key}')?$expand=RecipientPermission` | Partial | Original Get-EXORecipientPermission identity read; current command/recipient scope. No compiled permission filter/list profile claimed. |
| `GET /adminapi/beta/{tenant}/CasMailbox('{key}')` | Partial | Compiled Get-EXOCasMailbox; persisted flags. |
| `GET /adminapi/beta/{tenant}/Mailbox('{key}')/Exchange.GetMailboxStatistics()` | Partial | Compiled primary/archive statistics; absent archive not invented. |
| `GET /adminapi/beta/{tenant}/Mailbox('{key}')/MailboxFolder/Exchange.GetMailboxFolderStatistics(folderscope=Exchange.ElcFolderType'{scope}')` | Partial | Compiled projection over explicitly provisioned folders. |

| Microsoft command | MockAzure support | Notes / unsupported parts |
| --- | --- | --- |
| [Get-OrganizationConfig](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-organizationconfig?view=exchange-ps) | Partial: read | Tenant identity plus imported audit, OAuth client and bounded organization settings. Missing optional fields stay absent. Tenant-wide command authority required. |
| [Get-TransportConfig](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-transportconfig?view=exchange-ps) | Partial: read | Typed imported transport settings, optional tenant Identity; no transport engine or public settings writes. |
| [Get-AcceptedDomain](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-accepteddomain?view=exchange-ps) | Partial: read | Imported authoritative/internal-relay domains; Identity/ResultSize. No domain verification or external relay. |
| [Get-TransportRule](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-transportrule?view=exchange-ps) | Partial: read | Imported name, priority, state, mode, author/timestamps and bounded conditions/actions; Identity/State/ResultSize. No Filter/DlpPolicy or rule execution. |
| [Get-MailboxPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-mailboxpermission?view=exchange-ps), [Get-RecipientPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-recipientpermission?view=exchange-ps) | Partial: read | Canonical mailbox scope; imported inherited/deny ACL rows and SendAs evidence. Identity, literal User/Trustee filters and ResultSize; recipient AccessRights=SendAs. ACL evidence grants no command authority. Imported rows remain display-only; operational grants are also visible. No Owner/soft-deleted/unresolved switches; compiled identity reads are listed below. |
| [Get-Recipient](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-recipient?view=exchange-ps), [Get-EXORecipient](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exorecipient?view=exchange-ps) | Partial: read | Canonical provisioned UserMailbox/SharedMailbox/RoomMailbox/EquipmentMailbox. UUID, Exchange GUID, UPN, SMTP/alias and unambiguous display name. Classic Anr/SortBy, ResultSize and one eq/ne/-like predicate; compiled eq/ne and pages up to 1,000. No contacts, mail users, distribution lists or group-mailbox enumeration. Missing explicit identity fails rather than returning all. |
| [Get-EXOMailboxPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exomailboxpermission?view=exchange-ps), [Get-EXORecipientPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exorecipientpermission?view=exchange-ps) | Partial: identity read | Unchanged module 3.11.0, actual private wire DTOs; imported and operational ACLs. No unscoped listing, compiled User/Trustee filters or ResultSize profile claimed. |
| [Add-MailboxPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/add-mailboxpermission?view=exchange-ps), [Remove-MailboxPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-mailboxpermission?view=exchange-ps) | Partial: stateful mutation | FullAccess only; canonical users/security groups, rejects directory-name/SMTP-alias collisions, current nested membership, deny precedence and documented inheritance flags. AutoMapping stored/audited, no Autodiscover. Does not grant SendAs or command authority. |
| [Add-RecipientPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/add-recipientpermission?view=exchange-ps), [Remove-RecipientPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-recipientpermission?view=exchange-ps) | Partial: stateful mutation | SendAs only; current user/security-group trustees with ambiguous identity rejection, same-transaction native audit facts. Operational authority is separate from imported ACL evidence. The send checker is available for planned mail workflows; Graph sending is not yet exposed. |
| [Get-Mailbox](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-mailbox?view=exchange-ps), [Get-EXOMailbox](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exomailbox?view=exchange-ps) | Partial: read | Identity, selected properties/property sets, result-size/archive profile. |
| [Set-Mailbox](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-mailbox?view=exchange-ps) | Partial: stateful mutation | ForwardingAddress, ForwardingSmtpAddress, DeliverToMailboxAndForward and GrantSendOnBehalfTo. Delegation replacement/null/Add/Remove; canonical IDs returned by Get-Mailbox. Atomic current authorization and audit facts. Other mailbox settings fail explicitly. |
| [Get-EXOMailboxStatistics](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exomailboxstatistics?view=exchange-ps), [Get-EXOMailboxFolderStatistics](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exomailboxfolderstatistics?view=exchange-ps), [Get-EXOCasMailbox](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exocasmailbox?view=exchange-ps) | Partial: read | Provisioned primary/archive folder counts/sizes/CAS flags; not a message-content store. |
| [Get-InboxRule](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-inboxrule?view=exchange-ps), New-InboxRule, Set-InboxRule, Remove-InboxRule, Enable-InboxRule, Disable-InboxRule | Implemented within rule profile | Shared Graph/Exchange state, ordering, supported conditions/actions/exceptions, hidden-rule reads, command-specific authority. No rule execution. |
| [Get-SweepRule](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-sweeprule?view=exchange-ps), New-SweepRule, Set-SweepRule, Remove-SweepRule | Implemented within rule profile | Persisted configuration; bounded sender/folder/age/exception parameters. |
| [Get-MessageTraceV2](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-messagetracev2?view=exchange-ps), [Get-MessageTraceDetailV2](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-messagetracedetailv2?view=exchange-ps) | Partial: read | Imported rows/details. UTC, 90-day lookback, max 10-day range, default 1000/max 5000 rows; EndDate/StartingRecipientAddress continuation. Duplicate Received+RecipientAddress pairs rejected on import. |
| [Search-UnifiedAuditLog](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/search-unifiedauditlog?view=exchange-ps) | Partial: stateful search | Canonical UAL evidence; StartDate/EndDate, Operations/UserIds/ObjectIds/IPAddresses/SiteIds/RecordType/FreeText, Formatted, ResultSize, SessionId/SessionCommand. Default 100/max 5,000 per page; preview 5,000, large-set 50,000, mixed-mode 10,000. Durable tenant/actor/filter-bound snapshots; rechecks current authority. Local session lifetime one hour, 32 MiB snapshot, 1,000 active sessions per tenant. Unsupported HighCompleteness/LegacyFlow/LongerRetentionEnabled; bounded documented output fields and pinned record types. See [audit search contract](docs/mail-contracts.md#exchange-unified-audit-search). |
| [Get-AdminAuditLogConfig](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-adminauditlogconfig?view=exchange-ps) | Partial: read | No parameters. Cloud-only ingestion setting; gates native Exchange publication and both UAL search APIs, retains history. |
| [Get-AuthenticationPolicy](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-authenticationpolicy?view=exchange-ps) | Partial: read | `Identity`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Get-InboundConnector](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-inboundconnector?view=exchange-ps) | Partial: read | `Identity`, `ResultSize`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Get-OrganizationRelationship](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-organizationrelationship?view=exchange-ps) | Partial: read | `Identity`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Get-OutboundConnector](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-outboundconnector?view=exchange-ps) | Partial: read | `Identity`, `IncludeTestModeConnectors`, `IsTransportRuleScoped`, `ResultSize`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Get-RemoteDomain](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-remotedomain?view=exchange-ps) | Partial: read | `Identity`, `ResultSize`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Get-SharingPolicy](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-sharingpolicy?view=exchange-ps) | Partial: read | `Identity`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [New-AuthenticationPolicy](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/new-authenticationpolicy?view=exchange-ps) | Partial: create | `Name`, `AllowBasicAuthActiveSync`, `AllowBasicAuthAutodiscover`, `AllowBasicAuthImap`, `AllowBasicAuthMapi`, `AllowBasicAuthOfflineAddressBook`, `AllowBasicAuthOutlookService`, `AllowBasicAuthPop`, `AllowBasicAuthPowershell`, `AllowBasicAuthReportingWebServices`, `AllowBasicAuthRpc`, `AllowBasicAuthSmtp`, `AllowBasicAuthWebServices`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [New-InboundConnector](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/new-inboundconnector?view=exchange-ps) | Partial: create | `Name`, `SenderDomains`, `CloudServicesMailEnabled`, `Comment`, `ConnectorSource`, `ConnectorType`, `Enabled`, `RequireTls`, `RestrictDomainsToCertificate`, `RestrictDomainsToIPAddresses`, `SenderIPAddresses`, `TlsSenderCertificateName`, `TreatMessagesAsInternal`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [New-OrganizationRelationship](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/new-organizationrelationship?view=exchange-ps) | Partial: create | `Name`, `DomainNames`, `DeliveryReportEnabled`, `Enabled`, `FreeBusyAccessEnabled`, `FreeBusyAccessLevel`, `MailTipsAccessEnabled`, `MailTipsAccessLevel`, `PhotosEnabled`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [New-OutboundConnector](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/new-outboundconnector?view=exchange-ps) | Partial: create | `Name`, `AllAcceptedDomains`, `CloudServicesMailEnabled`, `Comment`, `ConnectorSource`, `ConnectorType`, `Enabled`, `IsTransportRuleScoped`, `RecipientDomains`, `RouteAllMessagesViaOnPremises`, `SenderRewritingEnabled`, `SmartHosts`, `TestMode`, `TlsDomain`, `TlsSettings`, `UseMXRecord`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [New-RemoteDomain](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/new-remotedomain?view=exchange-ps) | Partial: create | `Name`, `DomainName`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [New-SharingPolicy](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/new-sharingpolicy?view=exchange-ps) | Partial: create | `Name`, `Domains`, `Default`, `Enabled`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Remove-AuthenticationPolicy](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-authenticationpolicy?view=exchange-ps) | Partial: delete | `Identity`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Remove-InboundConnector](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-inboundconnector?view=exchange-ps) | Partial: delete | `Identity`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Remove-OrganizationRelationship](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-organizationrelationship?view=exchange-ps) | Partial: delete | `Identity`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Remove-OutboundConnector](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-outboundconnector?view=exchange-ps) | Partial: delete | `Identity`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Remove-RemoteDomain](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-remotedomain?view=exchange-ps) | Partial: delete | `Identity`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Remove-SharingPolicy](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-sharingpolicy?view=exchange-ps) | Partial: delete | `Identity`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Set-AdminAuditLogConfig](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-adminauditlogconfig?view=exchange-ps) | Partial: update | `Force`, `UnifiedAuditLogIngestionEnabled`. Cloud-only ingestion setting; gates native Exchange publication and both UAL search APIs, retains history. |
| [Set-AuthenticationPolicy](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-authenticationpolicy?view=exchange-ps) | Partial: update | `Identity`, `AllowBasicAuthActiveSync`, `AllowBasicAuthAutodiscover`, `AllowBasicAuthImap`, `AllowBasicAuthMapi`, `AllowBasicAuthOfflineAddressBook`, `AllowBasicAuthOutlookService`, `AllowBasicAuthPop`, `AllowBasicAuthPowershell`, `AllowBasicAuthReportingWebServices`, `AllowBasicAuthRpc`, `AllowBasicAuthSmtp`, `AllowBasicAuthWebServices`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Set-InboundConnector](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-inboundconnector?view=exchange-ps) | Partial: update | `Identity`, `CloudServicesMailEnabled`, `Comment`, `ConnectorSource`, `ConnectorType`, `Enabled`, `Name`, `RequireTls`, `RestrictDomainsToCertificate`, `RestrictDomainsToIPAddresses`, `SenderDomains`, `SenderIPAddresses`, `TlsSenderCertificateName`, `TreatMessagesAsInternal`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Set-OrganizationRelationship](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-organizationrelationship?view=exchange-ps) | Partial: update | `Identity`, `DeliveryReportEnabled`, `DomainNames`, `Enabled`, `Force`, `FreeBusyAccessEnabled`, `FreeBusyAccessLevel`, `MailTipsAccessEnabled`, `MailTipsAccessLevel`, `Name`, `PhotosEnabled`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Set-OutboundConnector](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-outboundconnector?view=exchange-ps) | Partial: update | `Identity`, `AllAcceptedDomains`, `CloudServicesMailEnabled`, `Comment`, `ConnectorSource`, `ConnectorType`, `Enabled`, `IsTransportRuleScoped`, `Name`, `RecipientDomains`, `RouteAllMessagesViaOnPremises`, `SenderRewritingEnabled`, `SmartHosts`, `TestMode`, `TlsDomain`, `TlsSettings`, `UseMXRecord`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Set-RemoteDomain](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-remotedomain?view=exchange-ps) | Partial: update | `Identity`, `AllowedOOFType`, `AutoForwardEnabled`, `AutoReplyEnabled`, `DeliveryReportEnabled`, `IsInternal`, `MeetingForwardNotificationEnabled`, `Name`, `NDREnabled`, `TargetDeliveryDomain`, `TNEFEnabled`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Set-SharingPolicy](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-sharingpolicy?view=exchange-ps) | Partial: update | `Identity`, `Default`, `Domains`, `Enabled`, `Name`. Persisted tenant configuration; Name/GUID identities, current command authority. No external protocol execution. |
| [Get-RoleGroup](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-rolegroup?view=exchange-ps) | Partial: operational RBAC | `Identity`, `ResultSize`. Three built-in groups/five selected roles; current membership and command authority. User/SecurityGroup assignments, Organization or direct RecipientGroupScope writes; no App/custom/delegating/policy/OU scopes. See [role contract](docs/mail-contracts.md#exchange-role-management). |
| [Get-RoleGroupMember](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-rolegroupmember?view=exchange-ps) | Partial: operational RBAC | `Identity`, `ResultSize`. Three built-in groups/five selected roles; current membership and command authority. User/SecurityGroup assignments, Organization or direct RecipientGroupScope writes; no App/custom/delegating/policy/OU scopes. See [role contract](docs/mail-contracts.md#exchange-role-management). |
| [Add-RoleGroupMember](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/add-rolegroupmember?view=exchange-ps) | Partial: operational RBAC | `Identity`, `Member`, `BypassSecurityGroupManagerCheck`. Three built-in groups/five selected roles; current membership and command authority. User/SecurityGroup assignments, Organization or direct RecipientGroupScope writes; no App/custom/delegating/policy/OU scopes. See [role contract](docs/mail-contracts.md#exchange-role-management). |
| [Remove-RoleGroupMember](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-rolegroupmember?view=exchange-ps) | Partial: operational RBAC | `Identity`, `Member`, `BypassSecurityGroupManagerCheck`. Three built-in groups/five selected roles; current membership and command authority. User/SecurityGroup assignments, Organization or direct RecipientGroupScope writes; no App/custom/delegating/policy/OU scopes. See [role contract](docs/mail-contracts.md#exchange-role-management). |
| [Get-ManagementRoleAssignment](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-managementroleassignment?view=exchange-ps) | Partial: operational RBAC | `Identity`, `Role`, `RoleAssignee`, `RoleAssigneeType`, `Enabled`, `Delegating`, `RecipientGroupScope`. Three built-in groups/five selected roles; current membership and command authority. User/SecurityGroup assignments, Organization or direct RecipientGroupScope writes; no App/custom/delegating/policy/OU scopes. See [role contract](docs/mail-contracts.md#exchange-role-management). |
| [New-ManagementRoleAssignment](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/new-managementroleassignment?view=exchange-ps) | Partial: operational RBAC | `Name`, `Role`, `User`, `SecurityGroup`, `RecipientRelativeWriteScope`, `RecipientGroupScope`, `Force`. Three built-in groups/five selected roles; current membership and command authority. User/SecurityGroup assignments, Organization or direct RecipientGroupScope writes; no App/custom/delegating/policy/OU scopes. See [role contract](docs/mail-contracts.md#exchange-role-management). |
| [Remove-ManagementRoleAssignment](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-managementroleassignment?view=exchange-ps) | Partial: operational RBAC | `Identity`, `Force`. Three built-in groups/five selected roles; current membership and command authority. User/SecurityGroup assignments, Organization or direct RecipientGroupScope writes; no App/custom/delegating/policy/OU scopes. See [role contract](docs/mail-contracts.md#exchange-role-management). |
| Other unlisted cmdlets | Unsupported | No general PowerShell command execution/remote PowerShell endpoint. |

Exchange.ManageAsApp / delegated Exchange.Manage do not bypass command authority.
Exchange entitlement and supported roles/grants remain required; Purview roles are separate.
See [mail contracts](docs/mail-contracts.md) and [qualification](docs/compatibility.md),
including original versus patched collector profiles and upstream output defects.
Endpoint support is not full Hawk/MES certification. Typed Exchange collection evidence
is provisioned and round-tripped through `/_mock/tenants/{tenant_id}/mail/collectionState`;
see the [command/field/parameter manifest](integrations/contracts/exchange-mail.json).

## Azure Resource Manager and Activity Logs

Use the **management** origin and listed `api-version`, not Graph. `{scope}` is an owned
subscription/resource-group/resource scope supported by the inventory. Management groups
and arbitrary provider execution are not implied. RBAC uses Reader, Contributor, Owner
and User Access Administrator with inherited access/current grant checks.

| Microsoft REST URI | MockAzure support | Accepted api-version | Notes / Microsoft reference |
| --- | --- | --- | --- |
| `/tenants`, `/subscriptions`, `/subscriptions/{subscription}` | Implemented: GET | `2020-01-01`, `2021-01-01` | Authorized local discovery. [Subscriptions](https://learn.microsoft.com/en-us/rest/api/resources/subscriptions/list?view=rest-resources-2021-01-01). |
| `/subscriptions/{subscription}/resourceGroups` | Implemented: GET | `2021-04-01` | SQL-filtered inventory with effective access. [List groups](https://learn.microsoft.com/en-us/rest/api/resources/resource-groups/list?view=rest-resources-2021-04-01). |
| `/subscriptions/{subscription}/resourceGroups/{group}` | Implemented: GET/HEAD/PUT/PATCH/DELETE | `2021-04-01` | Durable async deletion, local Location/Retry-After and worker. No real deployment. [Groups](https://learn.microsoft.com/en-us/rest/api/resources/resource-groups?view=rest-resources-2021-04-01). |
| `/subscriptions/{subscription}/resources`<br>`/subscriptions/{subscription}/resourceGroups/{group}/resources` | Partial: GET | `2021-04-01` | Imported inventory; not individual provider-resource GET/PUT/DELETE. Pages invalidate on inventory mutation. [Resources](https://learn.microsoft.com/en-us/rest/api/resources/resources/list?view=rest-resources-2021-04-01). |
| `{scope}/providers/Microsoft.Resources/tags/default` | Implemented: GET/PUT/PATCH/DELETE | `2021-04-01`, `2024-07-01` | Replace/merge/delete; tag names case-insensitive, values case-sensitive. [Tags](https://learn.microsoft.com/en-us/rest/api/resources/tags/update-at-scope?view=rest-resources-2021-04-01). |
| `{scope}/providers/Microsoft.Authorization/roleDefinitions` and `/{id}` | Partial: GET | `2022-04-01`, `2022-05-01-preview` | 20 pinned built-ins: four control roles plus selected Storage/Key Vault data roles; no custom/conditional-role writes. [Definitions](https://learn.microsoft.com/en-us/rest/api/authorization/role-definitions/list?view=rest-authorization-2022-04-01). |
| `{scope}/providers/Microsoft.Authorization/roleAssignments` and `/{id}` | Implemented: GET collection; GET/PUT/DELETE item | `2022-04-01`, `2020-08-01-preview` | Local principals; immutable scope/principal/role identity. Bounded filters/paging; no conditional/delegated grants. [Assignments](https://learn.microsoft.com/en-us/rest/api/authorization/role-assignments/list-for-scope?view=rest-authorization-2022-04-01). |
| `/subscriptions/{subscription}/providers/Microsoft.Insights/eventtypes/management/values` | Implemented: GET | `2015-04-01` | Imported/native Activity Logs; 100ns times. Required eventTimestamp ge, bounded end-time/dimension filters, select/snapshot nextLink; cap 100/page. [Activity](https://learn.microsoft.com/en-us/rest/api/monitor/activity-logs/list?view=rest-monitor-2015-04-01). |
| `/subscriptions/{subscription}/providers/microsoft.resources/operations/{id}` | Local polling: GET | Follow returned Location | Local resource-group deletion completion; not general Microsoft provider operations. |
| `/subscriptions/{subscription}/providers` | Partial: GET | `2021-04-01` | Five provider profiles with selected type/version/location metadata; current subscription read permission. No expand/aliases. Resource CRUD is separately qualified, not implied by discovery. [List](https://learn.microsoft.com/en-us/rest/api/resources/providers/list?view=rest-resources-2021-04-01). |
| `/subscriptions/{subscription}/providers/{namespace}` | Partial: GET | `2021-04-01` | Current per-subscription Registered/NotRegistered state; case-insensitive namespace; unknown namespaces/versions fail. [Get](https://learn.microsoft.com/en-us/rest/api/resources/providers/get?view=rest-resources-2021-04-01). |
| `/subscriptions/{subscription}/providers/{namespace}/register` | Partial: POST | `2021-04-01` | Current provider register action, persisted synchronous completion, Activity Log fact and internal service event. No registration consent properties. [Register](https://learn.microsoft.com/en-us/rest/api/resources/providers/register?view=rest-resources-2021-04-01). |
| `/subscriptions/{subscription}/providers/{namespace}/unregister` | Partial: POST | `2021-04-01` | Current unregister action; existing provider inventory blocks unregistration. Denials commit no state/evidence. [Unregister](https://learn.microsoft.com/en-us/rest/api/resources/providers/unregister?view=rest-resources-2021-04-01). |
| Provider resource paths, deployments, diagnosticSettings, metrics, Azure Resource Graph, Log Analytics query, storage/Key Vault data planes | Unsupported | None claimed | Inventory/Activity Logs do not imply deployment, diagnostics, KQL or data-plane emulation. |

Reserved path segments are case-insensitive. Percent-encoded ARM paths and unknown
provider/version combinations are rejected. [ARM contracts](docs/arm-contracts.md) and
[operation catalog](src/mockazure/providers/arm/data/operations.json) contain exact schemas,
version-specific sources and local paging rules.

## Other Microsoft cloud APIs

| Microsoft API URI | MockAzure status | Notes / reference |
| --- | --- | --- |
| `POST https://api.security.microsoft.com/api/advancedhunting/run` | Unsupported | Legacy Defender XDR hunting transport; Microsoft documents its transition/retirement in favor of Graph. Neither this route nor Graph runHuntingQuery is implemented. [Reference](https://learn.microsoft.com/en-us/defender-xdr/api-advanced-hunting). |
| `POST https://manage.office.com/api/v1.0/{tenant}/activity/feed/subscriptions/start?contentType=...` and `/subscriptions/stop` | Partial: implemented app-only pull profile | Client-isolated enabling/disabling; all five types, separate DLP permission; current identity and subscription enforcement. No webhooks/delegated access. [Contract](integrations/contracts/activity-feed.md). |
| `GET .../activity/feed/subscriptions/list` and `/subscriptions/content?contentType=...` | Partial: implemented app-only pull profile | Raw arrays; publication windows ≤24h, seven-day retention, snapshot `NextPageUri` header; publisher quota/AF429/Retry-After. [Reference](https://learn.microsoft.com/en-us/office/office-365-management-api/office-365-management-activity-api-reference). |
| `GET .../activity/feed/audit/{contentId}` | Implemented: GET within the pull profile | Immutable common-schema audit arrays, original event IDs, current subscription/DLP checks and expiry. Canonical native UAL publication + explicit feed-only imports; preserved workload extensions. No claims of all product-specific schema rules. |
| `.../activity/feed/subscriptions/notifications` and webhook delivery | Unsupported | Pull subscriptions do not imply notification history or external delivery. |

## Populating data and runtime discovery

These are **MockAzure-only**, not Microsoft endpoints. Except health/readiness, they
require the separate admin token. Provider operations use their local service access token.

| Local endpoint / control | Purpose / boundary |
| --- | --- |
| GET/PUT `/_mock/tenants/{tenant}/cloud/providerState` | Typed provider registration and event export/replace/reset; validates tenant/subscription ownership; restored events are historical and cannot revive delivery. No account keys or secret values. |
| GET `/_mock/health`, `/_mock/ready`, `/_mock/capabilities` | Health, storage/key readiness, authenticated high-level discovery. This document records method/path boundaries beyond that summary. |
| POST `/_mock/tenants/{tenant}/directory/import`, GET `/_mock/tenants/{tenant}/directory/export` | Public directory fixtures, not complete identity/credential/authorization backups. |
| POST `/_mock/tenants/{tenant}/evidence/import`, GET `/_mock/tenants/{tenant}/evidence/export` | Supported historical families including UAL/Activity; [controls](docs/evidence.md) govern publication, retention/reset. |
| POST `/_mock/tenants/{tenant}/collections/{family}/import` | Five security collections; atomic 1–1000 records, 8 MiB limit, replacement by ID at a new revision. No collection delete/reset/export/pruning integration yet. |
| `/_mock/tenants/{tenant}/activity/{state,settings,import,publish,drain,export,restore,reset,cleanup}` | Feed publication faults, bounded evidence lifecycle, generation fences; [methods and limits](integrations/contracts/activity-feed.md). |
| POST `/_mock/tenants/{tenant}/workloads/{workload}/import`, GET `/export?scope=...`, POST `/reset` under the same workload prefix | `sharepoint`, `teams`, `intune` typed evidence; JSON/NDJSON/ZIP export, explicit revision/generation reset and validated restore through import. [Site/drive](integrations/contracts/site-drive.md), [Teams](integrations/contracts/teams.md), [Intune](integrations/contracts/intune.md). |
| POST/DELETE `/_mock/tenants/{tenant}/workloads/sharepoint/grants[/{grant}]`, POST/DELETE `.../workloads/teams/access[/{grant}]` | POST collection / DELETE item only. Authoritative site user/app ACLs and Teams membership/RSC, separate from imported display evidence. |
| POST `/_mock/tenants/{tenant}/content/uploads`, PUT `/uploads/{upload}/chunks/{ordinal}`, POST `/uploads/{upload}/complete` under the content prefix | Tenant-local binary upload/finalization referenced by mail, drive and hosted-content imports; not Graph upload support. [Content storage](docs/content-storage.md). |
| GET/PUT `/_mock/tenants/{tenant}/identity/sessions/{session}/authentication-context` | Trusted simulated device/platform binding for policy tests; bearer claims and request headers cannot establish it. [Policy profile](integrations/contracts/authentication-policies.md). |
| Identity, licensing, methods, mailbox, ARM, managed-host provisioning | Configure state/entitlements through CLI/documented admin routes. [Identity](docs/identity.md), [mail](docs/mail-contracts.md), [ARM](docs/arm-contracts.md). |

An empty response from an implemented route means no matching visible local data; a missing
route is different. Provision identities, grants, licenses/entitlements and evidence as
required. A vendored schema type or recognized permission alone does not establish coverage.

## Evidence behind this map and maintenance

Reviewed against concrete handler dispatch, authorization/query checks and integration
contracts/tests, not only registered route names. Several wildcard routes dispatch many
operations, and some registered methods deliberately reject every call; route registration
alone is not proof of support. [Audit findings and verification](docs/coverage-audit.md)
record the corrections made to this map.

The qualified expansion passed **1,153 unit/integration tests, with 1 skipped**, all ten
combined original-client stages, and the 100,000-record/two-replica recovery profile.
Both final-head CI runs at `8580605` passed all ten jobs each. See
[coverage qualification](docs/coverage-expansion-results.md) and
[compatibility](docs/compatibility.md) for evidence and original-versus-patched collector
limits. These checks do not prove every Microsoft field, permission or query combination.

Implementation/test entry points:

- [Registration](src/mockazure/admin/app.py), [directory](src/mockazure/providers/graph/directory.py), [writes](src/mockazure/providers/graph/writes.py), [delta](src/mockazure/providers/graph/delta.py), [batch](src/mockazure/providers/graph/batch.py).
- [Security routers](src/mockazure/providers/graph/security), [evidence](src/mockazure/providers/graph/evidence/routes.py), [licensing](src/mockazure/providers/graph/licensing.py), [collections](src/mockazure/collections/routes.py).
- [Mail content](src/mockazure/providers/graph/mail_content_routes.py), [SharePoint](src/mockazure/providers/graph/site_routes.py), [drives](src/mockazure/providers/graph/drive_routes.py), [Teams](src/mockazure/providers/graph/team_routes.py), [Intune](src/mockazure/providers/graph/intune_routes.py), [Activity feed](src/mockazure/activity/routes.py).
- [Rules](src/mockazure/providers/graph/mail_routes.py), [UAL](src/mockazure/providers/graph/audit_query_routes.py), [Exchange](src/mockazure/providers/exchange), [ARM](src/mockazure/providers/arm).
- [Integration tests](tests/integration), [collector call inventory](integrations/contracts/collection-coverage.json), [schema provenance](src/mockazure/providers/graph/schemas/README.md).

Update this map with each endpoint change: method, exact path, version, authorization,
query limits, data population and tests. Move a gap to implemented only when the behavior
exists. Keep API operations, local controls and qualified collector workflows distinct.

SharePoint/OneDrive query limits, authority, import/export lifecycle and client qualification
are specified in [the site/drive contract](integrations/contracts/site-drive.md), with
[pinned operation sources](integrations/contracts/site-drive.json). These adapters are v1.0 only. Optional import keys distinguish parent-scoped public IDs
(such as version `1.0` under multiple files). ZIP exports include typed metadata, binary
hashes and stored bytes; restore validates tenant identity and finalized local content.

Teams query, membership/RSC, archive and client limits are detailed in the
[Teams contract](integrations/contracts/teams.md) and its
[pinned operation sources](integrations/contracts/teams.json).
