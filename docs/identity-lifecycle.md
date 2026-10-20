# Operational identity lifecycle

All operations use local PostgreSQL identity state and local OAuth endpoints.
Fixtures do not bypass permissions. Use the configured Graph endpoint in
`@odata.id` references; no reference is fetched from Microsoft.

## User access and sessions

User create, PATCH, `accountEnabled`, group/member/owner changes, supported license
assignments, application-role assignments and delegated grants retain their existing
operation-specific permissions. New token issuance uses current grants. Removing a
grant does not rewrite permission claims in an already-issued access token.
Disabling a user prevents new sign-ins, refresh and protected delegated requests.

`POST /v1.0/users/{id-or-UPN}/revokeSignInSessions` returns
`{"@odata.context":"…/$metadata#Edm.Boolean","value":true}`.
The delegated `/v1.0/me/revokeSignInSessions` variant is also available.
Both accept an empty body and no query options. Application callers need
`User.RevokeSessions.All`; delegated callers may also use `User.ReadWrite.All` or
`Directory.ReadWrite.All`. Cross-user delegated calls additionally enforce the
supported authentication-administrator hierarchy, including privileged targets.

Revocation advances `signInSessionsValidFromDateTime` using wall time, independently
of the evidence clock. Read it with
`GET /v1.0/users/{id}?$select=id,signInSessionsValidFromDateTime`.
It invalidates existing local refresh handles, authenticated browser transactions
and unredeemed authorization codes. Locally issued access tokens bound to those
login sessions also fail protected requests immediately. This is MockAzure's bounded
local behavior, not a claim that Microsoft invalidates every issued access token
immediately. Standalone access tokens without a local session retain their existing
expiration/current-identity checks. A fresh successful sign-in creates new authority.
External guest home-tenant sessions are not claimed revoked.

Successful local revocations publish sanitized directory audit facts. Denied calls
leave authority and the revocation watermark unchanged.

## Manager navigation

`PUT /v1.0/users/{id-or-UPN}/manager/$ref` accepts one `@odata.id` pointing to a
tenant-local user. `DELETE` on the same URI removes the reference and leaves both
users intact. Application permissions are `User.ReadWrite.All` or
`Directory.ReadWrite.All`; delegated writes also require the supported User
Administrator or Global Administrator role.

`GET /v1.0/users/{id-or-UPN}/manager` and `GET /v1.0/me/manager` return the manager.
The pinned Microsoft v1.0 contract supports **delegated reads only**, with
`User.Read.All` or a higher supported directory/user read permission. `$select`
works. Missing managers return 404. Organizational contacts and expanded manager
chains are not implemented. Cycles, foreign references and hierarchies deeper than
1,000 are rejected atomically. Manager changes publish sanitized directory audits.

Contracts and exact source hashes are in
[the living-tenant inventory](../src/mockazure/living_tenant/data/operations.json).

## Deleted objects and restoration

Use `GET /v1.0/directory/deletedItems/microsoft.graph.user`,
`.application` or `.servicePrincipal` to list deleted objects of that type.
The type cast is required by Microsoft's v1.0 contract. Lists support `$select`,
`$top` (1–999, default 100) and caller-bound `@odata.nextLink` paging. Other query
options are rejected. Continuations exclude newly deleted objects and recheck
current availability: restored, purged or expired items cannot reappear.
Item GET supports `$select`.

`POST /v1.0/directory/deletedItems/{id}/restore` restores the same ID.
For users, `newUserPrincipalName` can resolve a name conflict, and
`autoReconcileProxyConflict: true` removes stored proxy addresses that conflict with
active local users. Names must use the local tenant domain. Conflicts roll back
the entire restoration. `DELETE /v1.0/directory/deletedItems/{id}` permanently
removes recovery availability while retaining inert identity IDs for historical
references. All recovery endpoints enforce 30-day wall-time retention directly;
a worker is not needed to prevent expired-object access.

Reads require the relevant user/application read permission. User restore/purge
requires `User.DeleteRestore.All` or a supported higher permission; application/SP
restore/purge supports `Application.ReadWrite.All`, application
`Application.ReadWrite.OwnedBy` with saved ownership, or `Directory.ReadWrite.All`.
Delegated writes additionally check the published supported administrator roles.
Privileged-user recovery requires additional scope and a higher supported role.

| Object | Restored dependencies | Dependencies that do not return |
| --- | --- | --- |
| User | Saved ordinary static-group membership/ownership, application/device ownership and manager if the same tenant-local objects remain available and no cycle/conflict results | Sessions, authorization codes, consent, licenses, directory roles, role-assignable group edges or device registration observations |
| Application | Saved ownership where the owner remains live | Associated service principal, revoked credentials, consent, roles or prior browser/session authority |
| Service principal | Saved live ownership; backing application must already be restored | Revoked credentials, consent, roles or prior browser/session authority |

Group recycle-bin operations and managed-identity recovery are outside this
profile. Deleting an application also deletes its live local service principal;
restore them explicitly in that order. Application deletion revokes its local
credentials; provision a new credential and explicitly grant consent after
restoration. A restored application cannot redeem an authorization code issued
before deletion, even if the same consent is granted again.
Standalone access tokens without a local login session retain the existing
expiration/current-identity behavior; recovery does not claim to revoke every
previously issued bearer.

Upgrade recovery for already-deleted identities uses only retained public state:
missing ownership, memberships, consent and credentials are not reconstructed.
These legacy objects restore disabled and require explicit enablement. Sanitized
restore/purge audit events preserve actor and target references.

## Local guest invitations

`POST /v1.0/invitations` accepts the sourced invitation fields:
`invitedUserEmailAddress`, local `inviteRedirectUrl`, optional
`invitedUserDisplayName`, `invitedUserType: "Guest"`, `sendInvitationMessage`,
`invitedUserMessageInfo`, and the explicit `resetRedemption`/`invitedUser.id` variant.
Member invitations and sponsors are outside this profile. Email validation uses
the bounded ASCII character profile in Microsoft's invitation reference.

The API creates a canonical guest with `externalUserState: "PendingAcceptance"`.
Pending guests cannot authenticate or use protected resources. Ordinary
reinvitation preserves the guest ID and existing acceptance. Resetting redemption
requires `User.ReadWrite.All` or `Directory.ReadWrite.All`, retains membership and
assignments, invalidates local sessions, and supersedes old redemption links.
An email belonging to another active guest cannot be retargeted.

The returned `inviteRedeemUrl` points to the **authenticated local POST**
`/_mock/tenants/{tenant}/guests/{invitation}/redeem`, not an external identity
provider. Call it with the normal lab management token or authenticated console
session/CSRF headers. It changes the local guest to `Accepted`, returns the
configured redirect URL as data, and grants no consent, roles or memberships.
Repeating redemption is safe; superseded links return 409. No external sign-in,
home-tenant session revocation, email delivery or redirect fetch is performed.

Inviting requires `User.Invite.All` or a supported higher user/directory write
permission. The local policy endpoint
`GET/PUT /_mock/tenants/{tenant}/guests/policy` exposes `enabled` and
`allow_member_invites` Booleans (both default true). Disabling invitations blocks
app-only callers too. Restricting member invitations requires a supported Guest
Inviter, Directory Writers, User Administrator or Global Administrator delegated
role. Delegated redemption reset requires Helpdesk Administrator, User
Administrator or Global Administrator.

Only configured browser origins are accepted for redirect destinations.
`sendInvitationMessage: true` stores a local plain-text delivery record, including
at most one CC recipient. Browse `identity.guestInvitations` in the service console
to see its delivery mode, message, status and related guest. No SMTP transport is
used. The users browser also displays user type and external acceptance state.

For testing guest API access, explicitly provision an `identity.clients` named
delegated fixture for the accepted credentialless guest. The existing local
password/PKCE/consent flow then issues tokens; ordinary group/site/Teams checks
still decide access. Invitation creation emits an `Invite external user` audit.
Local acceptance emits an `Update user` audit with `local-guest-redemption`
provenance; it does not assert that an external identity provider authenticated
the guest.
## Certificate rollover

`POST /v1.0/applications/{id}/addKey` and `removeKey`, and the corresponding
`servicePrincipals` actions, operate on the same credentials used by OAuth.
Both object-ID paths and `applications(appId='{client-id}')` /
`servicePrincipals(appId='{client-id}')` paths work, including the
`microsoft.graph.` action aliases. Initial bootstrap remains an explicit
permission-checked `PATCH` of `keyCredentials`.

The supported certificate profile is public RSA (2048 bits or larger),
`AsymmetricX509Cert` / `Verify`. Importing private certificates with
`X509CertAndPassword` / `Sign` is unsupported. Keep client private keys outside
data templates, exports and the repository.

Every action verifies an RS256 JWT signed with a currently valid certificate
already belonging to that object. Its issuer is the **object ID**, its audience
is `00000002-0000-0000-c000-000000000000`, and its `nbf`/`exp` window must be
at most ten minutes and contain the current security wall time. A new certificate
cannot sign its own addition. Missing, expired, future, revoked, wrong-object
or invalid proofs fail without changing credentials.

For calls managing another application or service principal, use
`Application.ReadWrite.OwnedBy` (application token and owned object),
`Application.ReadWrite.All`, or `Directory.ReadWrite.All`. Delegated callers
additionally need ownership or a supported application-administrator role.
An application can roll **its own application object's** keys without an extra
Graph permission, but still needs a valid access token, live identity and proof.
Service-principal actions use their documented permission table.
Managed identity credentials remain host-controlled.

This recipe uses an already-authenticated local `httpx.Client` named `graph`,
with its base URL set to MockAzure and its CA trusted. `object_id` is the
application or service-principal object ID; `old_private_key` and `new_certificate`
are local cryptography objects. Do not print the access token, proof or private key.

```python
import base64
import time
import jwt
from cryptography.hazmat.primitives.serialization import Encoding

def rollover_proof(private_key, object_id):
    now = int(time.time())
    return jwt.encode(
        {"aud": "00000002-0000-0000-c000-000000000000",
         "iss": str(object_id), "nbf": now, "exp": now + 600},
        private_key, algorithm="RS256",
    )

path = f"/v1.0/applications/{object_id}"  # or servicePrincipals
added = graph.post(path + "/addKey", json={
    "keyCredential": {
        "type": "AsymmetricX509Cert", "usage": "Verify",
        "displayName": "Replacement certificate",
        "key": base64.b64encode(new_certificate.public_bytes(Encoding.DER)).decode(),
    },
    "passwordCredential": None,
    "proof": rollover_proof(old_private_key, object_id),
})
added.raise_for_status()
new_key_id = added.json()["keyId"]
# Acquire a fresh local OAuth token with the new private key before removing the old key.
# Sign removal with either currently valid key, normally the verified replacement.
removed = graph.post(path + "/removeKey", json={
    "keyId": old_key_id,
    "proof": rollover_proof(new_private_key, object_id),
})
removed.raise_for_status()
```

For password rotation, call `addPassword`, retain its one-time `secretText` in
local client credential storage, acquire a fresh token with it, then call
`removePassword` with the old `keyId`. Normal reads and native directory audits
never disclose `secretText`. Removal prevents subsequent token acquisition;
already issued access tokens retain their ordinary lifetime and authorization
checks. Certificate audit records contain only sanitized metadata differences.

## Privileged access

The v1.0 PIM profile separates eligible assignments, requests, schedules and
instances. Eligibility alone grants no access. Active schedules contribute to
the same role checks used by Graph evidence, licensing, authentication methods,
Conditional Access, consent, directory writes and Exchange authorization.
Each check uses current security wall time and live principal state. A stopped
worker cannot extend a role. Deactivation and account disablement take effect
on subsequent requests with the same token; independent permanent grants remain.
Deleting an identity revokes its schedules, so restoring it does not restore PIM
authority. Disabled administrators retain privileged-target protections even
though they cannot exercise their roles.

Use a tenant with the local Entra P2 inventory entitlement. This profile does not
model separate Entra ID Governance licensing or per-user PIM license compliance.
Only the built-in role definitions and tenant directory scope `/` are supported:

- Eligibility requests: user `adminAssign` and `adminRemove`.
- Assignment requests: administrative user/service-principal `adminAssign` and
  `adminRemove`; delegated eligible-user `selfActivate` and `selfDeactivate`.
- Requests, schedules and instances: collection/item reads. Request collections
  also support delegated `filterByCurrentUser(on='principal')`.
- Policies and policy assignments: collection/item reads; policy rule collection
  and item reads, and bounded rule PATCH.

Administrative requests require the appropriate `RoleEligibilitySchedule.*` or
`RoleAssignmentSchedule.*` permission, or `RoleManagement.ReadWrite.Directory`.
Delegated administrators additionally need Privileged Role Administrator or
Global Administrator. Self actions require the matching signed-in principal
and current eligibility covering the complete activation window. The ordinary
read-role checks apply to broad reads; own-request filtering does not expose
other users' requests.

Default local role policies require justification and expiration. Activation
defaults to at most eight hours and a completed local MFA session; administrators
can configure expiration (at most 24 hours for activation, 366 days for bounded
administrative schedules), justification, ticket requirements and MFA through
the corresponding expiration/enablement rules. MFA requires an actual local
authorization-code session and, when method enforcement is enabled, a currently
registered eligible method outcome. A request field or fabricated MFA header
cannot satisfy it.

Approval requirements can be represented, but approval-required activation
returns `501` and creates no grant. Approval stages, approval execution,
authentication-context rules, notifications, custom extensions, group eligibility,
custom roles/scopes, recurring schedules, validation-only requests, update,
renewal and extension actions remain unsupported.

Immediate requests become `Provisioned`. Future administrative grants are
`Granted` until their start time and cannot authorize early. Only a future
`Granted` request can be canceled; an active assignment must be deactivated or
administratively removed. Canceled requests become unavailable after 30 days; collection access cleans up
the expired request records. Immutable audit facts remain available.
Overlapping schedules of the same kind/principal/role return `409`.

Collections support bounded equality filters, `$select` and caller-bound paging
(default 100, cap 999), without `$expand`. Mixed policy-rule collections have no
query options. Pagination snapshots describe inventory at collection time;
authorization always reevaluates current state. `/roleAssignments` includes
active schedules as distinct grants, while legacy role membership deduplicates
their principals. Delete PIM grants through schedule requests, not through the
permanent assignment DELETE API.

For a local original-client check, download and verify the two pinned Microsoft
Graph PowerShell modules and run:

```powershell
python -m tools.verify_living_pim --config .runtime/living-native/native.json --report work/living-pim.json --prepare-clients
```

The verifier creates disposable local identities, performs local PKCE and
registered simulated MFA, and runs the unchanged Microsoft Graph
Identity.Governance 2.35.1 cmdlets through eligibility → activation → allowed
audit read → deactivation → denied audit read. On Windows, its original client
runs in an isolated Linux container against the native HTTPS API; PostgreSQL
and the API retain their normal native configuration.
Omit `--prepare-clients` on subsequent runs to verify the local archives without
downloading them.

