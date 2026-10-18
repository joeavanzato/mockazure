# Local identity and authentication

The M2 profile includes app-only and delegated OAuth, persistent identity
administration, and the bounded SDK/collector connection contracts described in
compatibility.md. See identity-execution.md for verification and review evidence.

Run database migrations before explicitly initializing OAuth identity:

```powershell
mockazure db upgrade --config .runtime/instance/native.json
mockazure identity initialize --config .runtime/instance/native.json
```

Initialization creates identity.wrap in the configured instance directory and an
encrypted RSA signing key in PostgreSQL. Repeating initialization preserves the
same identity. Startup does not replace missing wrapping material. A previously
initialized instance without its correct wrapping key fails readiness and token
issuance. Foundation-only instances remain administratively usable before this
explicit initialization.

The CA/server TLS keys and OAuth signing keys are separate. Back up PostgreSQL
and the instance directory together. Tenant metadata exports are not identity
backups and contain no credentials, grants, sessions or private keys.

## Provisioning

Create a tenant through the existing tenant command. Identity input comes from
JSON files; avoid putting passwords or client secrets on command lines.

A user input file contains upn, display_name, password and optional enabled.
The UPN must use the local tenant's registered domain. Application input:

```json
{
  "display_name": "Local collector",
  "client_mode": "confidential",
  "required_permissions": [
    {
      "resource": "graph",
      "kind": "application",
      "permissions": ["Organization.Read.All"]
    }
  ]
}
```

Commands:

```text
mockazure identity create-user TENANT_ID --input user.json --config CONFIG
mockazure identity create-application TENANT_ID --input app.json --config CONFIG
mockazure identity issue-secret TENANT_ID CLIENT_ID --output secret.json --config CONFIG
mockazure identity register-certificate TENANT_ID CLIENT_ID --certificate client.pem --config CONFIG
mockazure identity set-grant TENANT_ID GRANT_ID --input grant.json --config CONFIG
mockazure identity revoke-grant TENANT_ID GRANT_ID --config CONFIG
mockazure identity revoke-credential TENANT_ID CREDENTIAL_ID --config CONFIG
mockazure identity list TENANT_ID users --config CONFIG
mockazure identity list TENANT_ID applications --config CONFIG
```

Grant JSON contains client_id, resource, kind, permissions and optional user_id.
The grant cannot exceed the application's configured permissions. Application
permissions and delegated scopes are distinct. New applications receive a
separate local service-principal record. Cross-tenant grants are constrained in
PostgreSQL as well as in the API.

Issued secrets appear once in the protected output file; the CLI prints only
credential metadata. It refuses to overwrite an existing file. Credential and
password verification uses bounded salted scrypt; list responses never expose
their verifiers. Register public RSA certificates of at least 2048 bits, never
client private keys.

## App-only protocol

Discovery: GET /TENANT/v2.0/.well-known/openid-configuration.
JWKS: GET /TENANT/discovery/v2.0/keys.
Token: POST /TENANT/oauth2/v2.0/token, form URL-encoded.

Send grant_type=client_credentials, client_id, scope=RESOURCE/.default and either
client_secret or client_assertion plus the JWT-bearer client_assertion_type.
Registered certificate assertions support RS256 and PS256 with x5t or x5t#S256.
Signature, client iss/sub, exact local token-endpoint audience, validity and jti
replay checks apply. Assertions have a maximum 600-second validity window; replay
state is durable and shared across replicas.

Resources are explicitly mapped independently of network endpoints. For example,
https://graph.microsoft.com/.default requests the Graph audience while all
requests still target local HTTPS. An unknown resource cannot initiate forwarding.

Access tokens last 3600 seconds with 60 seconds of validation clock tolerance.
The M2 JWT profile uses the at+jwt header type to distinguish access tokens from
ID tokens, a local issuer, tenant/client/object IDs and separate roles/scp
claims. This is a declared local token profile, not byte-for-byte reproduction of
every Microsoft access-token version. App-only grants never return refresh tokens.

Revoke a credential to stop subsequent issuance. Existing access-token claims are
not rewritten. Resource-specific authorization rules determine access; full
conditional-access enforcement belongs to milestone 3.

Rotate signing keys with:

```text
mockazure identity rotate-keys --config CONFIG
```

Previously issued tokens remain verifiable through their expiry plus clock
tolerance. The old public keys remain in JWKS for that window. Private keys never
appear in JWKS, ordinary exports or diagnostic output.

## Client boundaries

Pinned versions, source revisions and artifact hashes are in
integrations/manifest.json and uv.lock. Acquisition is separate from offline
qualification. Use tools/prepare_clients.py --verify to verify acquired archives
without network requests.

The current PowerShell client image is PowerShell 7.5.0. Python and PowerShell
qualification containers block external DNS, IPv4 and IPv6. The native Python
test harness independently blocks nonlocal sockets.

ExchangeOnlineManagement 3.11.0 normalizes a custom ConnectionUri to scheme and
hostname, dropping its port. Its bootstrap therefore needs HTTPS port 443 within
the isolated client network. The dedicated harness provides that endpoint
without changing host DNS, trust stores or foundation services. The real module
loads the approved Get-OrganizationConfig proxy and invokes the local API.

## Delegated flows and sessions

Authorization-code/S256, device authorization and refresh are implemented locally.
Registered redirects must be local HTTPS or native HTTP loopback callbacks. They
match exactly; no wildcard or dynamic-port matching is advertised. Common and
organizations routes resolve local registrations/users for delegated flows only.
Client-credentials requests use an explicit tenant authority. Interactive sign-in
is required for each browser authorization; silent browser SSO is not implemented.

Login, consent and simulated MFA use persistent transactions with a Secure,
HttpOnly, SameSite cookie and CSRF checks. Device approvals use the same forms.
User consent can grant configured User.Read; admin-required scopes need a local
Global Administrator or management preconsent. Simulated MFA is visibly a lab
outcome rather than a physical second factor. Fresh tenants require selection of an
eligible registered mobile SMS or Authenticator method to complete it. The current
method policy is checked again at token issuance and refresh. Upgraded tenants retain
the legacy generic outcome until their first successful method-policy write.
See [method policies and trusted device context](../integrations/contracts/authentication-policies.md)
and [Conditional Access](security-contracts.md) for the bounded enforcement profile.

Codes expire after 300 seconds and redeem once across replicas. Device requests
expire after 900 seconds; initial polling is five seconds, with durable slow_down
increments of five seconds. Access and ID tokens last 3,600 seconds. Refresh
handles are hashed and bound to a session with an absolute 24-hour deadline.
Concurrent refresh succeeds; older handles remain valid until that deadline or
session revocation. Refresh checks enabled identities and current consent again.
GET/DELETE /_mock/tenants/{tenant}/identity/sessions lists metadata/revokes sessions.
Existing access tokens retain their claims until expiry, subject to supported
resource-side user/role checks. No universal instantaneous access-token revocation
is claimed. Authentication successes and consent/MFA denial outcomes are persisted
without raw credentials and projected into supported audit/sign-in evidence.

## Reproduce the dedicated M2 lab

Use a project-local Python environment with the locked dev/client dependencies.
All commands below run from the checkout. They create only .runtime/m2 and client
state; the original foundation runtime stays separate.

```powershell
python tools/prepare_identity.py
python tools/prepare_clients.py --output work/vendor
# Create the isolated network and prove it before enabling clients.
docker compose -f compose.clients.yaml run --rm powershell
# Build the runtime and Python client images.
docker build -t mockazure:identity .
docker build -f integrations/Dockerfile.python -t mockazure:identity-python-clients .
docker compose -f compose.m2.yaml up -d --wait
python tools/prepare_identity.py --provision
```

For final qualification, build a clean wheel with uv build. Compute
source_fingerprint() from tools.verify_identity and pass it as Docker build argument
MOCKAZURE_SOURCE_FINGERPRINT, then recreate M2 with docker compose up -d --wait.
The verifier rejects an image built from a different source fingerprint.

```powershell
python tools/verify_identity.py --config .runtime/m2/instance/native.json --client-manifest integrations/manifest.json --report work/identity-report.json
pytest tests/acceptance/test_identity_distribution.py -q
```

The verifier creates a clean offline wheel environment, starts a native API replica,
checks concurrent code/refresh and rotation, restarts only the M2 database/API,
and reruns every pinned SDK/collector/isolation probe. It leaves sanitized reports
under work and synthetic credentials under .runtime/clients. Ordinary tenant
exports still contain tenant metadata only. For operational recovery, preserve a
coordinated PostgreSQL backup plus identity.wrap, TLS files and the admin credential.
Restoring the database without its wrapping key cannot restore signing ability.

See compatibility.md for exact client acquisition boundaries. Exchange connection
qualification is complete for the one approved discovery command; broad Exchange,
Graph, ARM evidence collection and conditional-access policies remain later work.

## Operational role and qualification boundaries

For the implemented Exchange Get-OrganizationConfig operation, application consent
alone is insufficient. Assign a tenant-owned service-principal role with
PUT /_mock/tenants/{tenant}/identity/applications/{client}/roles/Global%20Reader
(or Global%20Administrator); DELETE the same URL to revoke it. The API permission
and persisted role are checked independently at module download and invocation.
Directory Readers is not sufficient. This is a bounded role gate, not mailbox
scope/RBAC parity. The standard M2 provisioning command assigns Global Reader.

The separation follows Microsoft's [app-only Exchange role guidance](https://learn.microsoft.com/en-us/powershell/exchange/app-only-auth-powershell-v2).
Certificate enrollment stores only canonical public certificate PEM, even when
a caller accidentally includes other PEM blocks. Signing transactions share a
PostgreSQL advisory lock; rotation takes it exclusively and timestamps retirement
after acquisition so already issued tokens retain their verification window.

Client preparation and final acceptance compare every extracted module/collector
file to its pinned archive and reject missing, modified, or unexpected files,
including unpinned module versions. No upstream collector edits are permitted.
Connect-MockAzure rejects an existing Graph environment whose login or Graph
endpoint differs from the supplied profile before reading credentials. Use a new
PowerShell process to change profiles.
