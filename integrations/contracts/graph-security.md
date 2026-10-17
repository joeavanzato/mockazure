# M3C security client qualification contract

Status: qualification in progress. This contract defines gates, not a compatibility claim.

Use the pinned Graph v1.0 CSDL and operation-specific contracts in
docs/security-contracts.md. Preserve original MSAL 1.39.0, Graph Python 1.64.0 and
Graph PowerShell 2.35.1 implementations. Generated requests target only the local
HTTPS profile with explicit lab CA. The existing local Graph transport remaps
pinned collector Graph URLs and fails closed; it does not supply service results.

## Required workflows

Each native Python, container Python and container PowerShell profile exercises
application and delegated modes. Verify actual application/SP create/read/update/
delete, owner reference, generated credential validity/revocation through local
OAuth, application and delegated grants, directory role assignment/read/removal,
method inventory, policy/location CRUD and missing-permission errors. Device
writes use delegated access as required by the operation contract.

Runtime verification also covers local block/MFA/report-only issuance decisions,
immutable decision evidence and denied property disclosure, two API replicas and
workers, PostgreSQL/API restart and existing snapshot continuation, tenant isolation,
installed-wheel byte parity, immutable image/source fingerprints, and native/container
network rejection. Process exit alone is insufficient: exact public values, IDs,
state transitions and resulting files must be asserted.

## Original collector closures

Load pinned original declarations and their original serializers/helpers:
Hawk Get-HawkTenantAppAndSPNCredentialDetail; MES Get-ConditionalAccessPolicies,
Get-OAuthPermissionsGraph, Get-MFA, Get-AllRoleActivity. Verify CSV/JSON output
records, source hashes and expected fields, including negative access cases.
The existing telemetry-only and no-unconfigured-reconnect boundaries apply.
Get-PIMAssignments is a distinct function outside the supported tenant-role profile.

Source inspection identified two required API additions: authentication-method
registration reports and user signInActivity. Implement them from current persisted
state and actual authentication evidence, with their own focused tests before
qualification. Registered method descriptors do not prove physical MFA challenges.
Physical capability services (MFA/passwordless/SSPR and system preference) are
explicitly disabled in the descriptor profile; their non-nullable booleans are false.
The per-user sign-in summary must outlive evidence retention and separate latest
interactive/noninteractive attempts from latest successful access.

## Primary references

- [Registration report operation](https://learn.microsoft.com/en-us/graph/api/authenticationmethodsroot-list-userregistrationdetails?view=graph-rest-1.0)
- [Registration report schema](https://learn.microsoft.com/en-us/graph/api/resources/userregistrationdetails?view=graph-rest-1.0)
- [User sign-in activity](https://learn.microsoft.com/en-us/graph/api/resources/signinactivity?view=graph-rest-1.0)
- [Graph v1.0 application](https://learn.microsoft.com/en-us/graph/api/resources/application?view=graph-rest-1.0)
- [Graph v1.0 service principal](https://learn.microsoft.com/en-us/graph/api/resources/serviceprincipal?view=graph-rest-1.0)

## Collector invocation bounds

MES Get-MFA uses its default method/report collection with a UPN filter.
The optional IncludePhoneNumbers augmentation is not a qualification target:
source inspection found it adds phone fields outside its user-filter block,
reusing the previous output object for excluded users. The unchanged default
invocation still must produce exact method and registration CSV records. Phone
descriptor values are independently required through both generated SDKs.
No failed output gate is waived.
