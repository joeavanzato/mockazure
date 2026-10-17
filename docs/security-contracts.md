# Security API contracts

M3C implementation reference. This document describes the implemented profile;
the milestone still requires its remaining tasks and final qualification.

## Applications and devices

Applications use an object ID distinct from the OAuth client/app ID. Both object
and appId addressing reach the same application and service-principal state,
including owner references and password actions. Devices also support deviceId
addressing, with uniqueness enforced within the tenant.

Graph writes, administrative identity setup and local OAuth share normalized
identity, credential and grant rows. Bootstrap is configuration rather than
invented Graph activity. Graph mutations publish immutable audit facts through
the evidence outbox.

Secrets are returned only when created. Certificates are public RSA certificates;
default resource reads omit key bytes and explicit keyCredentials selection
returns public DER. No private key, secret or verifier enters audit evidence.
Failed credential replacement is transactional. Credential revocation affects
new authentication; issued access tokens retain their claim/lifetime semantics.
Deleting an application disables its local service principals and ordinary
administrative enabling cannot restore the deleted object.

Device creation and registered-owner writes require delegated
Directory.AccessAsUser.All and an appropriate role. Registered owners are users.
Cloud Device Administrator can enable/disable devices; Windows 365 Administrator
can change supported basic properties. Approved MDM-only properties are rejected.
The declared write profile does not yet include non-Windows app-only
extensionAttributes. Unsupported properties and unenforced switches such as
appRoleAssignmentRequired are errors, not successful no-ops.

Sources:
[device owners](https://learn.microsoft.com/en-us/graph/api/device-post-registeredowners?view=graph-rest-1.0),
[device updates](https://learn.microsoft.com/en-us/graph/api/device-update?view=graph-rest-1.0),
[device deletion](https://learn.microsoft.com/en-us/graph/api/device-delete?view=graph-rest-1.0).

## Effective grants

- oauth2PermissionGrants supports list/item/create, scope replacement and deletion.
  AllPrincipals and per-user Principal grants share the rows used by OAuth consent
  and refresh validation. The service-principal navigation collection is available.
- Service-principal appRoleAssignments and resource-side appRoleAssignedTo support
  list/item/create/delete through object ID or appId addressing. Each application
  permission has a stable opaque assignment ID. This is a relationship identifier,
  distinct from the official permission GUID.
- Grant collections use PostgreSQL snapshots with bounded pages, caller/tenant/
  query-bound continuations, and authorization on every page. The declared query
  profile includes equality filters on supported IDs, selection, top and count.
  Unsupported expansion and operators return explicit errors.
- An assignment created through Graph affects new client-credential token claims.
  Revocation does not rewrite existing token claims. Removing delegated consent
  prevents refresh from retaining the removed scopes.
- Application-permission creation requires AppRoleAssignment.ReadWrite.All plus
  a documented application/directory read permission. Delegated assignment of
  Microsoft Graph app roles additionally requires Privileged Role Administrator
  or Global Administrator. Ordinary application administration is insufficient.
- Delegated grant writes require DelegatedPermissionGrant.ReadWrite.All or
  Directory.ReadWrite.All and the applicable delegated administrative role.
  The limited Application Developer path requires ownership, self-only Principal
  consent and non-administrative scopes.
- Unknown permission IDs, foreign principals, mismatched relationship references
  and duplicate grants fail transactionally.
- Per-permission creation metadata is separate from grant authority. Existing
  administrative grants with unknown assignment creation times report null;
  new Graph assignments record their actual time. Metadata never grants access.
- Graph and the documented Exchange.ManageAsApp role use real permission GUIDs
  in configured permissions, resource catalogs and effective assignments.
  Existing delegated Exchange/ARM grants retain the issuer's declared scope
  names; this does not claim a complete Exchange or ARM permission catalog.

Sources:
[delegated grant creation](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-post?view=graph-rest-1.0),
[scope replacement](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-update?view=graph-rest-1.0),
[grant listing](https://learn.microsoft.com/en-us/graph/api/oauth2permissiongrant-list?view=graph-rest-1.0),
[application assignment](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-post-approleassignments?view=graph-rest-1.0),
[assignment deletion](https://learn.microsoft.com/en-us/graph/api/serviceprincipal-delete-approleassignments?view=graph-rest-1.0),
[Exchange application permission](https://learn.microsoft.com/en-us/powershell/exchange/connect-exo-powershell-managed-identity).

## Evidence

Native security audits validate against Graph v1.0, including typed public
differences. They publish through Graph and export/reimport under the same schema
version. Failed mutations do not add successful audit records. The collector JSON
limitation already accepted for original MES is unchanged; no additional
qualification exception has been introduced.
"
## Directory roles

Role definitions expose official built-in template identifiers and identity fields.
Role assignments use canonical user/service-principal roles with tenant scope
`/`; custom role permissions, scoped assignments and PIM remain unsupported.
Requests for unsupported definition properties fail explicitly. Modern assignment
writes require RoleManagement.ReadWrite.Directory and, for delegated callers,
Privileged Role Administrator or Global Administrator. Reads enforce the documented
operation-specific permissions and roles.

Legacy directoryRoles includes activated roles even when no member remains.
Member results disclose only id/type without the corresponding object read
permission. Modern role assignments support bounded principal or roleDefinition
expansion; collection snapshots preserve page contents and query bindings.

Sources: [role definitions](https://learn.microsoft.com/en-us/graph/api/rbacapplication-list-roledefinitions?view=graph-rest-1.0),
[role assignments](https://learn.microsoft.com/en-us/graph/api/rbacapplication-list-roleassignments?view=graph-rest-1.0),
[legacy members](https://learn.microsoft.com/en-us/graph/api/directoryrole-list-members?view=graph-rest-1.0),
[official role templates](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference).


## Authentication-method inspection

Graph users/{id-or-UPN}/authentication and me/authentication support methods,
passwordMethods, phoneMethods, emailMethods, microsoftAuthenticatorMethods and
fido2Methods collections and item reads. There are no optional query parameters
on this declared profile, following the v1.0 operation references.

Password inventory reflects the canonical user's password verifier; its public
password and createdDateTime are null. Other methods appear only after explicit
authenticated local enrollment. POST/GET
/_mock/tenants/{tenant}/security/users/{user}/authenticationMethods and DELETE
of a method ID configure or inspect descriptors. Public fields are validated
against the pinned v1.0 schema. Secret/seed/private-key imports, invented timestamps
and asserted attestation outcomes are rejected. Enrollment records are configuration,
not evidence of a successful authentication challenge. SMS sign-in is not enabled
by adding a phone descriptor. A maximum of 100 non-password methods per user keeps
responses bounded; lookup has a tenant/user PostgreSQL index.

Generic and method-specific permissions use official identifiers. Self reads require
a supported delegated self or all-user permission; reading another user's inventory
requires an all-user permission and Global Reader, Authentication Administrator,
Privileged Authentication Administrator or Global Administrator for delegated callers.
Authentication Administrator receives masked phone numbers. The additional limited
password-reader role matrix is not implemented. Application access to concrete FIDO2
reads follows the current operation table's UserAuthMethod-Passkey.Read.All requirement;
a generic all-user methods permission is not accepted for that concrete endpoint.
Graph enrollment/reset/challenge mutations remain outside this milestone.

Sources: [generic methods](https://learn.microsoft.com/en-us/graph/api/authentication-list-methods?view=graph-rest-1.0),
[password methods](https://learn.microsoft.com/en-us/graph/api/authentication-list-passwordmethods?view=graph-rest-1.0),
[phone methods](https://learn.microsoft.com/en-us/graph/api/authentication-list-phonemethods?view=graph-rest-1.0),
[email methods](https://learn.microsoft.com/en-us/graph/api/authentication-list-emailmethods?view=graph-rest-1.0),
[Authenticator methods](https://learn.microsoft.com/en-us/graph/api/microsoftauthenticatorauthenticationmethod-list?view=graph-rest-1.0),
[FIDO2 methods](https://learn.microsoft.com/en-us/graph/api/fido2authenticationmethod-list?view=graph-rest-1.0).
