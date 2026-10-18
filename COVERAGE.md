# API coverage

Endpoint map for **MockAzure**, audited against merged source
[`072c407`](https://github.com/joeavanzato/mockazure/commit/072c407) on **2026-10-17**.
This describes implemented behavior, not milestone intentions or complete Microsoft
service parity. Microsoft links describe the real API; the support and limits columns
describe MockAzure. No real tenant is needed or contacted.

## Reading the tables

- **Implemented**: the listed methods function within the stated local profile.
  This does **not** mean every Microsoft property, permission variant or query works.
- **Partial**: a material restriction within the named surface.
- **Unsupported**: no implementation of the named operation.
- **Historical**: a working compatibility endpoint retained for older collectors.
- Methods apply only to the indicated path. `/{id}` means an item, not arbitrary children.
- Graph tables show **v1.0** paths. **Beta = Yes** means the listed operations also exist
  under `/beta`, not that every beta-only field/operation works. **No** means no beta route.
- Unlisted operations are **not claimed as supported**. Gap tables are priority examples,
  not an exhaustive catalog of Microsoft's entire API.

### Microsoft origins and local equivalents

| Service | Microsoft origin / addressing | MockAzure addressing |
| --- | --- | --- |
| Graph | `https://graph.microsoft.com` + path below | Configured Graph HTTPS origin + same path; default `https://localhost:8443` |
| Entra | `https://login.microsoftonline.com` + tenant protocol path | Configured login HTTPS origin + listed local path |
| ARM | `https://management.azure.com` + path and `api-version` | Configured management HTTPS origin + same path and supported version |
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
[gaps](#graph-gaps) · [Entra](#entra-authentication) · [Exchange](#exchange-online) ·
[ARM](#azure-resource-manager-and-activity-logs) · [data setup](#populating-data-and-runtime-discovery).

## Graph directory

Directory permissions, delegated roles, limited-information projections and current
identity state are enforced. The [directory contract](integrations/contracts/graph-directory.md)
contains the precise authorization/property allowlists.

| Microsoft REST URI | MockAzure support | Beta | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- | --- |
| `/v1.0/organization` | Partial: GET | Yes | Tenant ID, display name and verified domains plus 16 typed optional contact/address/sync fields provisioned through `directory/collectionState`; `$select`. `User.Read` returns null for the additional fields. No item route or Graph writes; full organization schema remains partial. | [Organization](https://learn.microsoft.com/en-us/graph/api/organization-list?view=graph-rest-1.0) |
| `/v1.0/me` | Implemented: GET | Yes | Delegated identity only; selected supported user fields. No PATCH on `/me`; use the supported user route. | [Get user](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0) |
| `/v1.0/users`<br>`/v1.0/users/{user}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Stateful ordinary local users, selected profile fields, account enablement, local-domain UPN changes and licensing fields. Item reads/writes resolve ID or UPN. No full guest/hybrid lifecycle or restore. | [Users](https://learn.microsoft.com/en-us/graph/api/resources/user?view=graph-rest-1.0) |
| `/v1.0/groups`<br>`/v1.0/groups/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Static security-group writes. No dynamic membership, Microsoft 365 group provisioning or role-assignable group writes. Permanent local deletion. | [Groups](https://learn.microsoft.com/en-us/graph/api/resources/group?view=graph-rest-1.0) |
| `/v1.0/users/$count`<br>`/v1.0/groups/$count` | Implemented: GET | Yes | Declared advanced-query headers required; no universal `/$count` handler. | [Advanced queries](https://learn.microsoft.com/en-us/graph/aad-advanced-queries) |
| `/v1.0/groups/{id}/members`<br>`/v1.0/groups/{id}/transitiveMembers` | Implemented: GET | Yes | Stored membership traversal; hidden-membership and object-read permissions enforced. No arbitrary type-cast path variants. | [Members](https://learn.microsoft.com/en-us/graph/api/group-list-members?view=graph-rest-1.0), [transitive](https://learn.microsoft.com/en-us/graph/api/group-list-transitivemembers?view=graph-rest-1.0) |
| `/v1.0/groups/{id}/members/$ref`<br>`/v1.0/groups/{id}/members/{member}/$ref` | Partial: POST first path; DELETE second | Yes | Add/remove local users or static security groups; cycle checks. No reference-free delete that also deletes a directory object. | [Add](https://learn.microsoft.com/en-us/graph/api/group-post-members?view=graph-rest-1.0), [remove](https://learn.microsoft.com/en-us/graph/api/group-delete-members?view=graph-rest-1.0) |
| `/v1.0/groups/{id}/owners` | Partial: GET | Yes | v1.0 omits service-principal owners; beta includes them. Initial owner bindings and v1.0 standalone owner-reference writes; see the expansion table below. | [Owners](https://learn.microsoft.com/en-us/graph/api/group-list-owners?view=graph-rest-1.0) |
| `/v1.0/users/{user}/memberOf`<br>`/v1.0/users/{user}/transitiveMemberOf` | Partial: GET | Yes | Group traversal with disclosure limits, not general role/administrative-unit navigation. Use explicit role APIs. `/me/memberOf` and `/me/transitiveMemberOf` aliases work in v1.0 with delegated tokens. | [Memberships](https://learn.microsoft.com/en-us/graph/api/user-list-memberof?view=graph-rest-1.0), [transitive](https://learn.microsoft.com/en-us/graph/api/user-list-transitivememberof?view=graph-rest-1.0) |
| `/v1.0/users/delta`<br>`/v1.0/groups/delta` | Implemented: GET | Yes | Durable rounds, selected fields, tombstones and group membership changes. Seven-day local state lifetime, then resync. No delta for other families. | [User delta](https://learn.microsoft.com/en-us/graph/api/user-delta?view=graph-rest-1.0), [group delta](https://learn.microsoft.com/en-us/graph/api/group-delta?view=graph-rest-1.0) |

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
| `/v1.0/devices`<br>`/v1.0/devices/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Stored inventory and bounded writes; creation requires delegated authority/role. No enrollment, compliance engine, Intune or arbitrary MDM-managed properties. | [Device](https://learn.microsoft.com/en-us/graph/api/resources/device?view=graph-rest-1.0) |
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
| `/v1.0/servicePrincipals/{id}/appRoleAssignments`<br>`/v1.0/servicePrincipals/{id}/appRoleAssignedTo` | Implemented: GET/POST collection; GET/DELETE `/{assignment}` | Yes | Official permission IDs, distinct assignment IDs; affects new tokens, not issued claims. AppId addressing supported. v1.0 user/group navigation is listed below. Imported user/group assignments appear in reverse reads but remain fixture evidence; they do not grant OAuth permissions and cannot be changed through SP grant writes. | [Assignments](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-list-approleassignments?view=graph-rest-1.0), [assigned principals](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-list-approleassignedto?view=graph-rest-1.0) |
| `/v1.0/directoryRoles`<br>`/v1.0/directoryRoles/{id}`<br>`/v1.0/directoryRoles/{id}/members` | Partial: GET | Yes | Activated built-in roles/members and template addressing. No legacy activation/member-reference writes; use modern assignments. | [Directory roles](https://learn.microsoft.com/en-us/graph/api/resources/directoryrole?view=graph-rest-1.0) |
| `/v1.0/roleManagement/directory/roleDefinitions`<br>`/v1.0/roleManagement/directory/roleDefinitions/{id}` | Partial: GET | Yes | 27 built-ins include description labels and source-pinned `rolePermissions.allowedResourceActions`. Descriptions are local labels; action metadata is descriptive and does not add a custom-role evaluator. No custom-role CRUD or complete definition schema. | [Definitions](https://learn.microsoft.com/en-us/graph/api/rbacapplication-list-roledefinitions?view=graph-rest-1.0) |
| `/v1.0/roleManagement/directory/roleAssignments`<br>`/v1.0/roleManagement/directory/roleAssignments/{id}` | Partial: GET/POST collection; GET/DELETE item | Yes | Tenant scope `/`, user/SP principals; current roles affect authorization. Bounded principal/roleDefinition expansion. No PIM/custom/scoped roles. | [Assignments](https://learn.microsoft.com/en-us/graph/api/rbacapplication-list-roleassignments?view=graph-rest-1.0) |

Legacy role item/member reads also accept `/directoryRoles(roleTemplateId='{template}')`
and `/directoryRoles/roleTemplateId={template}`.

## Graph authentication methods, reports and policy

| Microsoft REST URI | MockAzure support | Beta | Notes / unsupported parts | Microsoft reference |
| --- | --- | --- | --- | --- |
| `/v1.0/users/{user}/authentication/methods` | Partial: GET collection and `/{method}` | Yes | Also `/me/authentication/methods`. Descriptor inspection, not public enrollment/reset/challenges. No query options. | [Methods](https://learn.microsoft.com/en-us/graph/api/authentication-list-methods?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/passwordMethods` | Partial: GET collection and `/{method}` | Yes | Also `/me/...`. Reflects local password registration; password/createdDateTime null. No resetPassword action. | [Password](https://learn.microsoft.com/en-us/graph/api/authentication-list-passwordmethods?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/phoneMethods` | Partial: GET collection and `/{method}` | Yes | Also `/me/...`. Admin-provisioned descriptors; role-dependent phone masking. No SMS activation. | [Phone](https://learn.microsoft.com/en-us/graph/api/authentication-list-phonemethods?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/emailMethods` | Partial: GET collection and `/{method}` | Yes | Also `/me/...`. Admin-provisioned descriptors only. | [Email](https://learn.microsoft.com/en-us/graph/api/authentication-list-emailmethods?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/microsoftAuthenticatorMethods` | Partial: GET collection and `/{method}` | Yes | Also `/me/...`. No push delivery or physical MFA service. | [Authenticator](https://learn.microsoft.com/en-us/graph/api/microsoftauthenticatorauthenticationmethod-list?view=graph-rest-1.0) |
| `/v1.0/users/{user}/authentication/fido2Methods` | Partial: GET collection and `/{method}` | Yes | Also `/me/...`. No WebAuthn/attestation execution. Concrete app reads require declared passkey permission, not just generic methods scope. | [FIDO2](https://learn.microsoft.com/en-us/graph/api/fido2authenticationmethod-list?view=graph-rest-1.0) |
| `/v1.0/reports/authenticationMethods/userRegistrationDetails`<br>`/v1.0/reports/authenticationMethods/userRegistrationDetails/{id}` | Partial: GET | Yes | Derived from persisted registrations. AuditLog.Read.All, P1/P2 and delegated reader roles. Filter/continuation only; no select/top. No real SSPR service. | [Registration](https://learn.microsoft.com/en-us/graph/api/authenticationmethodsroot-list-userregistrationdetails?view=graph-rest-1.0) |
| `/v1.0/users?$select=id,signInActivity`<br>`/v1.0/users/{user}?$select=signInActivity` | Partial: GET | Yes | Also selected on `/me`. Last interactive/noninteractive attempts/success survives raw evidence retention. User access + AuditLog.Read.All, P1/P2 and applicable role. No arbitrary signInActivity filtering. | [Sign-in activity](https://learn.microsoft.com/en-us/graph/api/resources/signinactivity?view=graph-rest-1.0) |
| `/v1.0/identity/conditionalAccess/policies`<br>`/v1.0/identity/conditionalAccess/policies/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | Stored/enforced on supported delegated OAuth: user/group/resource/client-category/IP targets, block/MFA, disabled/report-only. P1/P2 and permissions/roles required. No risk/device/platform/session/authentication-strength controls. | [Policies](https://learn.microsoft.com/en-us/graph/api/conditionalaccessroot-list-policies?view=graph-rest-1.0) |
| `/v1.0/identity/conditionalAccess/namedLocations`<br>`/v1.0/identity/conditionalAccess/namedLocations/{id}` | Partial: GET/POST collection; GET/PATCH/DELETE item | Yes | IPv4/IPv6 IP locations; actual connection peer used. No country locations. Referenced locations cannot be deleted. | [Locations](https://learn.microsoft.com/en-us/graph/api/conditionalaccessroot-list-namedlocations?view=graph-rest-1.0) |

Descriptors are configured through emulator administration, not Graph enrollment.
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
| `/v1.0/security/auditLog/queries`<br>`/v1.0/security/auditLog/queries/{query}` | Implemented: GET/POST collection; GET item | Yes | Durable asynchronous search; worker required. AuditLogsQuery.Read.All or supported workload scopes, Audit Standard/Premium, separate Purview role for delegated callers. No update/delete/cancel. Beta uses bounded v1.0 profile. | [Queries](https://learn.microsoft.com/en-us/graph/api/security-auditcoreroot-list-auditlogqueries?view=graph-rest-1.0) |
| `/v1.0/security/auditLog/queries/{query}/records` | Implemented: GET | Yes | Retained results, stable paging/current authorization. Seven-day query lifetime, one-hour page handles. Imports and native mail-configuration audits. No direct `/security/auditLog/records` collection. | [Records](https://learn.microsoft.com/en-us/graph/api/security-auditlogquery-list-records?view=graph-rest-1.0) |

UAL filters cover time, workload, operation, type, UPN, IP, object, administrative unit
and bounded literal keywords. Imported SharePoint/OneDrive UAL records **do not** implement
SharePoint/OneDrive content APIs. See [mail contracts](docs/mail-contracts.md).

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
| Delta | Users/groups; select and issued state links | No arbitrary filter/top/order/expand. Local deletion/relationship semantics in directory contract. |
| `POST /v1.0/$batch` (also beta) | 1–20 independent GETs; per-item authorization/errors | Adds directoryObjects, apps/SPs/devices, roles/grants, identity policies, reports and security reads to the existing roots. Per-item 403/404/query errors and 429 remain visible. No writes/dependencies or external URLs. [Batching](https://learn.microsoft.com/en-us/graph/json-batching). |
| Throttling | Shared PostgreSQL budgets and one-shot read/page 429 for directory, app/device, role/grant/policy/report and security collection buckets; Exchange trace rolling window; ARM Activity faults | Retry-After; scope is tenant/client/resource and shared across API instances. `/me` shares the user bucket; batch charges inner calls only. Local quotas are configurable, not a promise to reproduce every Microsoft quota. |
| Continuations | Local origins; tenant/caller/query binding; reauthorization | Family-specific lifetime/snapshot behavior. Use nextLink unchanged. An odata.context value does not implement GET /$metadata. |
| Errors/authentication | Local token signature/audience/expiry/current identity checks; Graph-shaped errors | Missing provider route normally 501 MockAzureOperationNotImplemented; unsupported method 405; unsupported query usually 400. Missing objects/denied access are not coverage gaps. |

Query names above refer to OData parameters prefixed with `$`, such as `$select`.

## Graph gaps

Real API paths with no implementation, or unsupported operations on otherwise implemented
resources. Imports or similarly named Exchange commands do not make these Graph APIs work.

| Microsoft REST URI / operation | Status | What is missing | Microsoft reference |
| --- | --- | --- | --- |
| `/v1.0/users/{user}/messages`, `/v1.0/users/{user}/messages/{message}`, `/v1.0/users/{user}/mailFolders/{folder}/messages` | Unsupported | Message CRUD/bodies/delta. Inbox rules are supported separately. | [Messages](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0) |
| `/v1.0/users/{user}/messages/{message}/attachments` and message/attachment `/$value` | Unsupported | Attachments, raw MIME/content. | [Attachments](https://learn.microsoft.com/en-us/graph/api/message-list-attachments?view=graph-rest-1.0), [MIME](https://learn.microsoft.com/en-us/graph/outlook-get-mime-message) |
| `/v1.0/users/{user}/mailFolders`, `/v1.0/users/{user}/mailboxSettings`, `/v1.0/users/{user}/sendMail` | Unsupported | Folder APIs, Graph mailbox settings/sending. Exchange folder statistics/configuration are separate. | [Folders](https://learn.microsoft.com/en-us/graph/api/user-list-mailfolders?view=graph-rest-1.0), [settings](https://learn.microsoft.com/en-us/graph/api/user-get-mailboxsettings?view=graph-rest-1.0) |
| `/v1.0/directory/deletedItems` | Unsupported | Generic deleted-item reads/restore remain absent. | [Deleted items](https://learn.microsoft.com/en-us/graph/api/directory-deleteditems-list?view=graph-rest-1.0) |
| `/v1.0/applications/{id}/addKey`, `/v1.0/applications/{id}/removeKey` (also servicePrincipals) | Unsupported | Proof-of-possession actions; supported credential PATCH/admin provisioning is separate. | [Add key](https://learn.microsoft.com/en-us/graph/api/application-addkey?view=graph-rest-1.0) |
| `/v1.0/roleManagement/directory/roleEligibilityScheduleRequests`, `/v1.0/roleManagement/directory/roleAssignmentScheduleRequests` | Unsupported | PIM schedules/eligibility/activation; no custom/scoped role lifecycle. | [PIM](https://learn.microsoft.com/en-us/graph/api/resources/privilegedidentitymanagementv3-overview?view=graph-rest-1.0) |
| `/v1.0/policies/authenticationMethodsPolicy` and method write/action endpoints | Unsupported | Method policy, public enrollment/reset and real challenges. | [Method policy](https://learn.microsoft.com/en-us/graph/api/resources/authenticationmethodspolicy?view=graph-rest-1.0) |
| `/v1.0/sites`, `/v1.0/drives`, `/v1.0/users/{user}/drive`, drive item `/content` and `/permissions` | Unsupported | SharePoint/OneDrive content/permissions/versions/delta. | [Sites](https://learn.microsoft.com/en-us/graph/api/resources/site?view=graph-rest-1.0), [drives](https://learn.microsoft.com/en-us/graph/api/resources/drive?view=graph-rest-1.0) |
| `/v1.0/teams/{id}`, `/v1.0/chats`, `/v1.0/users/{user}/events`, `/v1.0/users/{user}/contacts` | Unsupported | Teams/chat, calendars/contacts. | [Teams](https://learn.microsoft.com/en-us/graph/api/resources/teams-api-overview?view=graph-rest-1.0), [calendars](https://learn.microsoft.com/en-us/graph/outlook-calendar-concept-overview) |
| `/v1.0/deviceManagement/managedDevices`, `/v1.0/deviceManagement/deviceCompliancePolicies`, `/v1.0/deviceManagement/deviceConfigurations` | Unsupported | Intune management/compliance; directory device objects are separate. | [Intune devices](https://learn.microsoft.com/en-us/graph/api/intune-devices-manageddevice-list?view=graph-rest-1.0) |
| `/v1.0/security/runHuntingQuery` | Unsupported | KQL/advanced hunting; imported alerts/incidents are not a hunting engine. | [Hunting](https://learn.microsoft.com/en-us/graph/api/security-security-runhuntingquery?view=graph-rest-1.0) |
| `/v1.0/security/cases/ediscoveryCases`, `/v1.0/security/labels/retentionLabels` | Unsupported | eDiscovery/retention operations; UAL search is the supported Purview-related surface. | [eDiscovery](https://learn.microsoft.com/en-us/graph/api/resources/security-ediscoverycase?view=graph-rest-1.0), [labels](https://learn.microsoft.com/en-us/graph/api/resources/security-retentionlabel?view=graph-rest-1.0) |
| `/v1.0/subscriptions`, `/v1.0/$metadata`; delta except users/groups | Unsupported | Graph notifications/webhooks, public metadata serving, extra delta APIs. ARM subscriptions are unrelated. | [Subscriptions](https://learn.microsoft.com/en-us/graph/api/resources/subscription?view=graph-rest-1.0), [metadata](https://learn.microsoft.com/en-us/graph/traverse-the-graph) |

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
| `GET /adminapi/beta/{tenant}/CasMailbox('{key}')` | Partial | Compiled Get-EXOCasMailbox; persisted flags. |
| `GET /adminapi/beta/{tenant}/Mailbox('{key}')/Exchange.GetMailboxStatistics()` | Partial | Compiled primary/archive statistics; absent archive not invented. |
| `GET /adminapi/beta/{tenant}/Mailbox('{key}')/MailboxFolder/Exchange.GetMailboxFolderStatistics(folderscope=Exchange.ElcFolderType'{scope}')` | Partial | Compiled projection over explicitly provisioned folders. |

| Microsoft command | MockAzure support | Notes / unsupported parts |
| --- | --- | --- |
| [Get-OrganizationConfig](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-organizationconfig?view=exchange-ps) | Partial: read | Bounded tenant bootstrap/configuration probe, not every organization setting. |
| [Get-Mailbox](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-mailbox?view=exchange-ps), [Get-EXOMailbox](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exomailbox?view=exchange-ps) | Partial: read | Identity, selected properties/property sets, result-size/archive profile. |
| [Set-Mailbox](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/set-mailbox?view=exchange-ps) | Partial: write | ForwardingAddress, ForwardingSmtpAddress, DeliverToMailboxAndForward; no delivery engine. |
| [Get-EXOMailboxStatistics](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exomailboxstatistics?view=exchange-ps), [Get-EXOMailboxFolderStatistics](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exomailboxfolderstatistics?view=exchange-ps), [Get-EXOCasMailbox](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-exocasmailbox?view=exchange-ps) | Partial: read | Provisioned primary/archive folder counts/sizes/CAS flags; not a message-content store. |
| [Get-InboxRule](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-inboxrule?view=exchange-ps), New-InboxRule, Set-InboxRule, Remove-InboxRule, Enable-InboxRule, Disable-InboxRule | Implemented within rule profile | Shared Graph/Exchange state, ordering, supported conditions/actions/exceptions, hidden-rule reads, command-specific authority. No rule execution. |
| [Get-SweepRule](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-sweeprule?view=exchange-ps), New-SweepRule, Set-SweepRule, Remove-SweepRule | Implemented within rule profile | Persisted configuration; bounded sender/folder/age/exception parameters. |
| [Get-MessageTraceV2](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-messagetracev2?view=exchange-ps), [Get-MessageTraceDetailV2](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-messagetracedetailv2?view=exchange-ps) | Partial: read | Imported rows/details. UTC, 90-day lookback, max 10-day range, default 1000/max 5000 rows; EndDate/StartingRecipientAddress continuation. Duplicate Received+RecipientAddress pairs rejected on import. |
| Search-UnifiedAuditLog, Get-TransportRule, Get-MailboxPermission, Get-RecipientPermission, other unlisted cmdlets | Unsupported | Graph UAL does not implement Search-UnifiedAuditLog. No general PowerShell command execution/remote PowerShell endpoint. |

Exchange.ManageAsApp / delegated Exchange.Manage do not bypass command authority.
Exchange entitlement and supported roles/grants remain required; Purview roles are separate.
See [mail contracts](docs/mail-contracts.md) and [qualification](docs/compatibility.md),
including original versus patched collector profiles and upstream output defects.
Endpoint support is not full Hawk/MES certification.

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
| `{scope}/providers/Microsoft.Authorization/roleDefinitions` and `/{id}` | Partial: GET | `2022-04-01`, `2022-05-01-preview` | Four built-ins; no custom-role writes. [Definitions](https://learn.microsoft.com/en-us/rest/api/authorization/role-definitions/list?view=rest-authorization-2022-04-01). |
| `{scope}/providers/Microsoft.Authorization/roleAssignments` and `/{id}` | Implemented: GET collection; GET/PUT/DELETE item | `2022-04-01`, `2020-08-01-preview` | Local principals; immutable scope/principal/role identity. Bounded filters/paging; no conditional/delegated grants. [Assignments](https://learn.microsoft.com/en-us/rest/api/authorization/role-assignments/list-for-scope?view=rest-authorization-2022-04-01). |
| `/subscriptions/{subscription}/providers/Microsoft.Insights/eventtypes/management/values` | Implemented: GET | `2015-04-01` | Imported/native Activity Logs; 100ns times. Required eventTimestamp ge, bounded end-time/dimension filters, select/snapshot nextLink; cap 100/page. [Activity](https://learn.microsoft.com/en-us/rest/api/monitor/activity-logs/list?view=rest-monitor-2015-04-01). |
| `/subscriptions/{subscription}/providers/microsoft.resources/operations/{id}` | Local polling: GET | Follow returned Location | Local resource-group deletion completion; not general Microsoft provider operations. |
| Provider resource paths, deployments, diagnosticSettings, metrics, Azure Resource Graph, Log Analytics query, storage/Key Vault data planes | Unsupported | None claimed | Inventory/Activity Logs do not imply deployment, diagnostics, KQL or data-plane emulation. |

Reserved path segments are case-insensitive. Percent-encoded ARM paths and unknown
provider/version combinations are rejected. [ARM contracts](docs/arm-contracts.md) and
[operation catalog](src/mockazure/providers/arm/data/operations.json) contain exact schemas,
version-specific sources and local paging rules.

## Other Microsoft cloud APIs

| Microsoft API URI | MockAzure status | Notes / reference |
| --- | --- | --- |
| `POST https://api.security.microsoft.com/api/advancedhunting/run` | Unsupported | Legacy Defender XDR hunting transport; Microsoft documents its transition/retirement in favor of Graph. Neither this route nor Graph runHuntingQuery is implemented. [Reference](https://learn.microsoft.com/en-us/defender-xdr/api-advanced-hunting). |
| `https://manage.office.com/api/v1.0/{tenant}/activity/feed/` + `subscriptions/start`, `subscriptions/list`, `subscriptions/stop` or `subscriptions/content` | Unsupported | Office 365 Management Activity feed subscriptions/content retrieval. Shared audit schemas and Graph UAL search do not implement this separate service. [Reference](https://learn.microsoft.com/en-us/office/office-365-management-api/office-365-management-activity-api-reference). |

## Populating data and runtime discovery

These are **MockAzure-only**, not Microsoft endpoints. Except health/readiness, they
require the separate admin token. Provider operations use their local service access token.

| Local endpoint / control | Purpose / boundary |
| --- | --- |
| GET `/_mock/health`, `/_mock/ready`, `/_mock/capabilities` | Health, storage/key readiness, authenticated high-level discovery. This document records method/path boundaries beyond that summary. |
| POST `/_mock/tenants/{tenant}/directory/import`, GET `/_mock/tenants/{tenant}/directory/export` | Public directory fixtures, not complete identity/credential/authorization backups. |
| POST `/_mock/tenants/{tenant}/evidence/import`, GET `/_mock/tenants/{tenant}/evidence/export` | Supported historical families including UAL/Activity; [controls](docs/evidence.md) govern publication, retention/reset. |
| POST `/_mock/tenants/{tenant}/collections/{family}/import` | Five security collections; atomic 1–1000 records, 8 MiB limit, replacement by ID at a new revision. No collection delete/reset/export/pruning integration yet. |
| Identity, licensing, methods, mailbox, ARM, managed-host provisioning | Configure state/entitlements through CLI/documented admin routes. [Identity](docs/identity.md), [mail](docs/mail-contracts.md), [ARM](docs/arm-contracts.md). |

An empty response from an implemented route means no matching visible local data; a missing
route is different. Provision identities, grants, licenses/entitlements and evidence as
required. A vendored schema type or recognized permission alone does not establish coverage.

## Evidence behind this map and maintenance

Checked against registered routes, method/query handlers and integration contracts/tests
at the source commit above. The merged PR's two CI runs each passed **445 unit tests and
418 PostgreSQL integration tests**, plus default install/recovery. Those totals are suite
evidence, not proof of every Microsoft API combination. See [qualification](docs/defaults-collection-results.md)
and [compatibility](docs/compatibility.md) for SDK/collector results and limits.

Implementation/test entry points:

- [Registration](src/mockazure/admin/app.py), [directory](src/mockazure/providers/graph/directory.py), [writes](src/mockazure/providers/graph/writes.py), [delta](src/mockazure/providers/graph/delta.py), [batch](src/mockazure/providers/graph/batch.py).
- [Security routers](src/mockazure/providers/graph/security), [evidence](src/mockazure/providers/graph/evidence/routes.py), [licensing](src/mockazure/providers/graph/licensing.py), [collections](src/mockazure/collections/routes.py).
- [Rules](src/mockazure/providers/graph/mail_routes.py), [UAL](src/mockazure/providers/graph/audit_query_routes.py), [Exchange](src/mockazure/providers/exchange), [ARM](src/mockazure/providers/arm).
- [Integration tests](tests/integration), [collector call inventory](integrations/contracts/collection-coverage.json), [schema provenance](src/mockazure/providers/graph/schemas/README.md).

Update this map with each endpoint change: method, exact path, version, authorization,
query limits, data population and tests. Move a gap to implemented only when the behavior
exists. Keep API operations, local controls and qualified collector workflows distinct.
