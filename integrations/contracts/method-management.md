# Authentication method management and password reset

Official v1.0 operation URLs and source hashes are in [the operation manifest](method-management.json).
Structural types are extracted from pinned Microsoft Graph CSDL. All calls stay local.

| Microsoft Graph route | Implemented operations | Boundaries |
| --- | --- | --- |
| `/users/{id or UPN}/authentication/phoneMethods` | POST; PATCH/DELETE `/{methodId}` | Fixed mobile/alternateMobile/office IDs. Immutable phoneType. Alternate requires mobile; remove alternate before mobile. |
| `/users/{id or UPN}/authentication/emailMethods` | POST; PATCH/DELETE `/{methodId}` | One recovery email; fixed ID. |
| `/users/{id or UPN}/authentication/microsoftAuthenticatorMethods/{id}` | DELETE | Deletes a stored descriptor, not a real push registration. |
| `/users/{id or UPN}/authentication/fido2Methods/{id}` | DELETE | Deletes a stored descriptor, not hardware or WebAuthn proof. |
| `/me/authentication/{supportedCollection}/{id}` | DELETE | Delegated self permission and a current local interactive session authenticated within ten minutes. Older sessions receive 403 insufficient_claims; sign in again. |
| `/users/{id or UPN}/authentication/methods/{passwordId}/resetPassword` | POST | Delegated administrators only; cannot reset self; cloud users only. Also accepts passwordMethods and the namespace-qualified microsoft.graph.resetPassword action emitted by original Graph PowerShell 2.35.1. |
| `/users/{id or UPN}/authentication/operations/{id}` | GET | Durable longRunningOperation, optional $select, current delegated permissions/roles and target binding. |

POST method creation returns 201 and a concrete method. PATCH/DELETE return empty 204.
Bodies use exact Graph fields and optional matching @odata.type; unsupported fields fail.
Inventory and registration reports reflect canonical changes. Successful changes append
queryable directoryAudit facts transactionally. No password, private key or OTP secret is
accepted in public method descriptors or audit facts. Existing read permissions and phone
masking remain unchanged. Writes target v1.0; beta writes are explicitly unsupported.

## Permission and role behavior

Application and delegated writes accept UserAuthenticationMethod.ReadWrite.All or the
matching UserAuthMethod-{Phone,Email,MicrosoftAuthApp,Passkey}.ReadWrite.All permission.
Delegated self deletion also accepts UserAuthenticationMethod.ReadWrite and the matching
family ReadWrite permission. These are distinct from family read-only scopes.

Other-user delegated writes require Authentication Administrator, Privileged Authentication
Administrator or Global Administrator. Authentication Administrator can manage only targets
allowed by Microsoft's password-reset hierarchy; every target role is checked. Members or
owners of role-assignable groups require privileged authority. Arbitrary custom roles and
administrative-unit scopes are outside this profile. Currently supported group creation
already excludes role-assignable groups; persisted protected relationships are still denied.

Password reset requires UserAuthenticationMethod.ReadWrite.All and the operation-specific
role hierarchy; User Administrator, Helpdesk Administrator and Password Administrator are
also supported for their permitted targets. Application tokens and self resets fail.
Status readers use the documented delegated read/write scopes and read roles. The operation
creator can poll with their still-current reset role, including the three additional reset
administrator roles; otherwise those roles could submit an operation without observing it.

Documentation conflicts are resolved explicitly: phone/email POST/PATCH prose says self-service
is unsupported, so broad permission tables do not enable it. The email DELETE page lists Global
Reader, but its authoritative role definition is read-only; it cannot mutate methods here.
Phone PATCH follows its empty 204 example despite prose referring to a returned object.

## Durable reset and local sign-in

The password method ID is `28c10230-6103-485e-b985-444c60001490`. Explicit newPassword returns
202 with an empty body; omission/null returns a random password once in passwordResetResponse.
Location and resourceLocation always point to the advertised local Graph origin. Responses
containing a generated password use no-store/no-cache. The local password profile accepts
12..4096 UTF-8 bytes without NUL; it does not claim Microsoft's full password-ban/history rules.

An operation and a leased job commit together. Only a salted verifier is persisted; the job
contains an operation ID. Publication/lease retry cannot choose or apply a second password.
A user has at most one pending reset. The operation clears its verifier on completion.
Failed terminal jobs are reconciled on status read or before a subsequent reset, releasing
obsolete verifier material and the pending-operation slot. Retained operation metadata is
not a password export. Database backups contain normal credential verifiers; public tenant
exports and Graph status never expose them.

Completed reset changes the canonical user verifier, requires password change, and increments
a credential epoch. Existing authorization-code/device approvals, refresh handles and delegated
sessions carrying the previous epoch cannot authorize further requests. This is an explicit
local revocation profile, not a claim about every Microsoft token revocation delay. New browser
sign-in accepts the temporary password only to enter the password-change form; cookie, CSRF,
transaction stage and credential epoch bind that one-use flow. Changing it increments the epoch
again, invalidating other temporary-password flows before consent and normal policy checks.
No real Microsoft tenant or hybrid password-writeback endpoint is contacted.

Unsupported: public Authenticator/FIDO2 enrollment or attestation, passwordless/OATH/TAP,
SMS-primary-sign-in activation, preferred/default MFA method configuration, real challenge
providers, hybrid password writeback, beta mutations, and arbitrary query options. Method
policies and extended Conditional Access are the separate next phase.

Qualification: `python -m tools.verify_method_management`, using pinned original Graph Python
SDK and Graph PowerShell modules and the actual HTTPS issuer. Each client performs eight
method mutations and delegated reset/status polling. The served password-change forms and
cookie/CSRF flow are exercised over HTTPS. Unit/integration tests also cover restart, lease
recovery, reset idempotence, code/device/refresh invalidation, role hierarchy, self-session
freshness, registration reports, and non-destructive upgrade from schema 049.
