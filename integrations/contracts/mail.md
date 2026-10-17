# M4 original client qualification

Pinned Microsoft.Graph.Mail 2.35.1 supplies the generated mailbox-rule commands.
The pinned Microsoft.Graph.Security 2.35.1 manifest has no auditLogQuery commands;
UAL uses Microsoft.Graph.Authentication's original Invoke-MgGraphRequest API.
Python uses the generated msgraph-sdk 1.64.0 audit query and mailbox builders.
Authentication uses real MSAL 1.39.0 against local endpoints.

ExchangeOnlineManagement 3.11.0 compiled Get-EXO commands remain unchanged.
Only the approved fixed command bootstrap supplies the listed service proxies.
The official Microsoft.Extensions.DependencyInjection.Abstractions 8.0.0 prerequisite
is loaded unchanged before the module. All public archives and extracted bytes
must match integrations/manifest.json.

Required checks: application/delegated Graph rule lifecycle/actions/page traversal,
exact UAL search/filter/1003-row traversal and native mutation evidence; original
Exchange primary/archive/folder/CAS values, proxy mutation cycle and 5003-row trace
continuation plus detail. Denied Graph scopes return errors.

Runtime gates additionally verify two clean-wheel API replicas, two container
workers, durable pending searches across API/PostgreSQL/worker restart, source
snapshot paging, current Purview role and entitlement denial, tenant separation,
429 Retry-After recovery and blocked external DNS/IPv4/IPv6 from clients.

Reports bind source fingerprint, Git commit, lock/manifest/wheel hashes, current
runtime/client images and every named literal pass value. Collector output gates
are added in Task9; a Task8 client report alone does not qualify M4.

The pinned raw PowerShell request provider does not attach its configured token
to a custom environment host. Harness UAL calls use its supported Headers parameter
with the same locally acquired token, matching the earlier directory profile.
Original collectors retain the existing Graph-LocalTransport host binding.
