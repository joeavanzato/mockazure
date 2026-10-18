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


## Conditional Access persistence

Policies and IP named locations support list/item/create/update/delete under
identity/conditionalAccess. Policies use official enabled, disabled and
enabledForReportingButNotEnforced states. Accepted targets are explicit tenant
users/groups, All users, resource appIds or All resources, declared client categories,
and IPv4/IPv6 named locations with All/AllTrusted selectors. Explicit exclusions
are retained for evaluation. Nested object PATCH preserves omitted members; arrays
replace their prior values.

Grant controls are block, mfa or compliantDevice with AND/OR operators; block cannot be combined with
another control. Unsupported nonempty risk/advanced-device/session/authentication
strength/guest targets and controls are rejected for all states, including
disabled/report-only. This stricter inspection profile avoids storing policies
that could later be enabled without an evaluator. Empty optional unsupported
conditions from standard SDK serialization are accepted as inactive.

Every operation requires an enabled P1/P2 service plan. Read uses Policy.Read.All.
Writes require Policy.ReadWrite.ConditionalAccess plus Policy.Read.All; policy
create/update also accepts Application.Read.All as the documented alternative
read permission. Delegated writes require Conditional Access Administrator,
Security Administrator or Global Administrator. The operation's documented
reader roles are supported, including the official Global Secure Access
Administrator template.

References must resolve in the tenant. A referenced named location cannot be
deleted until removed from policies. CIDRs are normalized to their network,
with the Graph address-family type checked. Mutations and policy-category audit
facts commit atomically. Lists use stable SQL snapshots, scalar filtering,
selection, ordering, count and top; skip and relationship expansion are outside
this profile. Count does not require the directory-specific ConsistencyLevel
header. Licence and role access is checked again on each continuation.

See [method policy and extended CA profile](../integrations/contracts/authentication-policies.md)
for role/platform targeting, trusted device binding, Intune compliance and upgrade boundaries.

Sources: [policy creation](https://learn.microsoft.com/en-us/graph/api/conditionalaccessroot-post-policies?view=graph-rest-1.0),
[policy update](https://learn.microsoft.com/en-us/graph/api/conditionalaccesspolicy-update?view=graph-rest-1.0),
[policy deletion](https://learn.microsoft.com/graph/api/conditionalaccesspolicy-delete?view=graph-rest-1.0),
[policy listing](https://learn.microsoft.com/en-us/graph/api/conditionalaccessroot-list-policies?view=graph-rest-1.0),
[named-location creation](https://learn.microsoft.com/graph/api/conditionalaccessroot-post-namedlocations?view=graph-rest-1.0),
[IP location update](https://learn.microsoft.com/en-us/graph/api/ipnamedlocation-update?view=graph-rest-1.0),
[grant controls](https://learn.microsoft.com/en-us/graph/api/resources/conditionalaccessgrantcontrols?view=graph-rest-1.0).

## Conditional Access enforcement and evidence

Delegated login, consent, MFA completion/skip, authorization-code redemption,
device approval/redemption and refresh evaluate the current stored policies.
The tenant security lock spans the final decision and token/evidence commit.
Transitive group membership and explicit exclusions determine user targeting;
resource appIds determine the requested service. The client category is the
persisted registration profile: public/device clients are mobile/desktop,
confidential authorization-code clients are browser. This is a declared local
classification, not user-agent inference. Refresh preserves the session category.

IP location matching uses the actual connection peer, including IPv4/IPv6.
Forwarded headers do not override it. If a relevant location cannot be evaluated
or the required licence is removed, a matching enabled policy fails closed.
Across enabled policies, block wins; fresh-profile MFA requires the local flow's selected, still-eligible enrollment outcome. Enrolled method descriptors alone never satisfy MFA. Report-only
policies record results without enforcing; disabled policies never apply.
A new policy is effective at redemption/refresh even for a pending older flow.

Sign-in facts capture the decision at the attempt, including policy ID/name,
controls, result and aggregate status. Later policy edits cannot rewrite it.
Block uses evidence code 53003, incomplete required MFA uses 50076, and explicit
MFA denial retains 50074. OAuth wire errors remain their declared local profile.
ClientAppUsed now reflects the known client category for delegated attempts;
unknown/app-only categories remain null.

Applied-policy details require Policy.Read.All, Policy.Read.ConditionalAccess
or Policy.ReadWrite.ConditionalAccess, plus an authorized delegated reader role
where applicable. The entire property is omitted before filtering/serialization
for unauthorized readers; collection and item reads use the same rule.
Report-only evolving enum members require Prefer: include-unknown-enum-members;
otherwise their public result is unknownFutureValue. Native evidence exports
preserve the original full decisions and can be imported into a separate instance.

Sources: [sign-in list and disclosure](https://learn.microsoft.com/en-us/graph/api/signin-list?view=graph-rest-1.0),
[applied policy results](https://learn.microsoft.com/en-us/graph/api/resources/appliedconditionalaccesspolicy?view=graph-rest-1.0).

## Identity and lifecycle boundaries

Managed identities retain host-controlled authentication. Graph inspection and
ordinary metadata edits do not turn their backing registration into a confidential
or public client. Platform and password/certificate edits are rejected for managed
backing applications and service principals. Deleted backing identities cannot be
reenabled through managed-identity administration, issue managed tokens, or use
previously issued tokens to access the API.

Deleting users, service principals, or applications removes affected owner
references transactionally. Historical stale references are omitted from current
owner reads and can still be explicitly removed. Live resource-principal owners
remain visible.

Conditional Access treats equivalent UUID spellings as the same identifier.
New policies store canonical UUIDs and reject equivalent duplicate targets;
evaluation and named-location deletion protection also normalize older persisted
policies. User/group/resource/location inclusions and exclusions share this rule.

Denied collector output is checked in every produced CSV and JSON artifact.
Only the documented all-False self-user MES CSV placeholder is permitted; nonempty
JSON records or malformed JSON fail the gate.
