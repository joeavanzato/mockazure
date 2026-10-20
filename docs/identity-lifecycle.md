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
