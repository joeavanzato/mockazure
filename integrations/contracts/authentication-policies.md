# Authentication method policy and Conditional Access profile

The operation manifest pins Microsoft Graph v1.0 sources and CSDL revision
`34b8e9c29032c82352a6fd7138be0a864a842bb2`. This profile simulates local operational
outcomes; it does not deliver SMS, contact Authenticator, or attest devices.

| URI | Operations | Supported behavior |
| --- | --- | --- |
| `/v1.0/policies/authenticationMethodsPolicy` | GET | Typed root and the two local configurations; no root PATCH. |
| `/v1.0/policies/authenticationMethodsPolicy/authenticationMethodConfigurations` | GET | SMS and Microsoft Authenticator configurations. |
| `/v1.0/policies/authenticationMethodsPolicy/authenticationMethodConfigurations/{Sms\|MicrosoftAuthenticator}` | GET, PATCH | `state`, `includeTargets`, `excludeTargets`; matching `@odata.type` required. |
| `/v1.0/identity/conditionalAccess/policies` and `/{id}` | Existing CRUD | Adds built-in role targeting, platform conditions, and `compliantDevice` grants. |

Configuration targets are existing local groups or `all_users` inclusion. Exclusions
win, including transitive group membership. Arrays replace previous targets. SMS supports
mobile MFA with `isUsableForSignIn=false`; Authenticator supports `push` or `any` simulated
push. `isRegistrationRequired=true`, passwordless/device-based push, registration campaigns,
advanced feature settings, arbitrary query options, other configurations and beta method
policy routes are unsupported. An accepted configuration never invents registrations.

Reads require `Policy.Read.AuthenticationMethod` or its write permission; root GET also
accepts `Policy.Read.All`. Writes require `Policy.ReadWrite.AuthenticationMethod`.
Delegated writes require Authentication Policy Administrator or Global Administrator;
Global Reader can read. These permissions differ from user-method management permissions.
Configuration changes and Policy-category audit facts commit together.

Fresh tenants enforce method policies. Existing tenants upgraded through migration 052
retain the legacy generic MFA profile until their first successful configuration PATCH.
Invalid writes do not activate it. This status is reported only by
`GET /_mock/tenants/{tenant}/identity/authentication-policy-profile` and capabilities,
never through an invented Graph field. No public operation switches enforcement off.

The browser offers eligible registered mobile SMS and Authenticator methods. Completing
MFA records the selected enrollment generation in server-maintained flow state. Eligibility
is checked again at completion, code/device redemption and refresh. Removing/recreating
a fixed phone ID does not restore an old outcome. Recovery email, FIDO2 descriptors and
caller-supplied MFA headers cannot satisfy this simulated challenge. Skipping optional
MFA remains possible when no applicable Conditional Access policy requires it.

Conditional Access adds `includeRoles`/`excludeRoles` using known built-in role template
IDs, and `platforms.includePlatforms`/`excludePlatforms` for android, iOS, windows,
windowsPhone, macOS, linux and `all`. Those extensions and compliantDevice writes are
v1.0 only. Advanced device filters, sign-in/user risk, session controls, authentication
strength, country/geolocation, guest and custom-role conditions remain rejected.

An administrator can bind an existing enabled local directory device and simulated
platform to an active authorization/device/login session via
`PUT /_mock/tenants/{tenant}/identity/sessions/{session}/authentication-context`.
This separate admin authority does not accept MFA assertions. Graph tokens and client
headers cannot perform that binding. `compliantDevice` succeeds only when that binding
still resolves to an enabled device and current licensed Intune observations report true
compliance. Unknown, conflicting, deleted and disabled state fail closed. Platform is
an explicit local simulation fact, not hardware attestation.

The evaluator supports AND/OR combinations of MFA and compliantDevice; block remains
exclusive. Current policies, role/group targeting, entitlement and observations are
rechecked on issuance/refresh. The security lock and Intune scope read lease span the
decision. Disabled policies do not apply; report-only results enter sign-in evidence
without denying the request. Policy evidence records the decision at that attempt.

Policy payloads can be collected through the typed Graph reads and audit exports.
Trusted authentication context is not a portable evidence assertion: database restart
preserves it, expiry cleanup removes it, and a fresh imported tenant must authenticate
again. Upgrade tests preserve legacy state without fabricating registrations. Downgrade
is refused when it would discard enabled enforcement, changed policies, or retained context.

Qualification entry point: `python -m tools.verify_authentication_policies`, using the
pinned original Graph Python SDK, Graph PowerShell and real local HTTPS issuer/forms.
See the generated run report for observed results; this document is a bounded contract.
