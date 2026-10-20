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
