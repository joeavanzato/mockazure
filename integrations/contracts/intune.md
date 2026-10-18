# Intune evidence contract

This is a bounded Microsoft Graph **v1.0** read profile over PostgreSQL. The
[operation manifest](intune.json) pins official REST sources and the CSDL revision.
Public management writes, wipe/retire/check-in actions, settings catalog, enrollment,
remote execution, policy evaluation on endpoints, scope tags/Intune RBAC, beta and
delta are unsupported. Imported assignment intent never becomes observed compliance.

## Entitlement and authority

Inventory and detected-app reads require delegated or application
`DeviceManagementManagedDevices.Read.All` (or ReadWrite.All). Configuration/compliance
policies, assignments and device statuses require the corresponding
`DeviceManagementConfiguration.Read.All` (or ReadWrite.All). The canonical permission
IDs and consent flags come from the pinned Microsoft permission reference. ReadWrite
permissions permit these reads; they do not enable public writes. Current application,
service principal and delegated user identities must remain active on every request.

A tenant requires an Enabled subscribed SKU with positive enabled capacity and a
successful `INTUNE_A` service plan (`c1ec4a95-1f05-45b3-a911-aa3fa01094f5`). User license
assignment is not required for this tenant API prerequisite. Local admin imports also
require that entitlement. Revoking capacity blocks reads and page continuations.
Delegated authority uses the broad Graph permission profile; Intune role assignments,
role scope groups/tags and per-administrator Intune RBAC restrictions are not simulated.

## Typed fixtures and relationships

`POST /_mock/tenants/{tenant}/workloads/intune/import` accepts the shared bounded import
with scope `deviceManagement`. Up to 1000 total records/edges/tombstones and 8 MiB per
request; all changes are atomic and optionally revision-checked. Supported kinds:
managedDevice, detectedApp, deviceConfiguration, deviceCompliancePolicy,
deviceConfigurationAssignment, deviceCompliancePolicyAssignment,
deviceConfigurationDeviceStatus and deviceComplianceDeviceStatus.

Configurations require one of windows10GeneralConfiguration,
iosGeneralDeviceConfiguration, androidGeneralDeviceConfiguration or
macOSGeneralDeviceConfiguration. Compliance policies require windows10CompliancePolicy,
iosCompliancePolicy, androidCompliancePolicy or macOSCompliancePolicy. Each payload
uses the official derived `@odata.type`; other derived types fail import. Required
properties are required on import. No runtime defaults invent settings or observations.
The pinned Windows scan-time properties omit CSDL Precision and therefore accept
HH:mm:ss at zero fractional precision ([OASIS CSDL 4.01, section 7.2.3](https://docs.oasis-open.org/odata/odata-csdl-xml/v4.01/cs01/odata-csdl-xml-v4.01-cs01.html)).

Explicit edges support user→managedDevices, detectedApp→managedDevices, and each
policy→assignments/deviceStatuses. User aliases resolve local UUIDs or UPNs; `/me`
requires delegated authentication. Device/profile relationships never grant authority.
Assignments/statuses belong to exactly one policy; local storage keys can distinguish
identical public child IDs under different policies. Public root keys equal their IDs.
Upserting a source replaces its outgoing edges; include retained edges in the batch.
Policy deletion must also remove its child evidence. No arbitrary content URLs are fetched.

`device_observation` maps managedDevice.azureADDeviceId to the canonical directory
**deviceId**, never the directory object's id. Missing/disabled directory devices,
ambiguous mappings, deleted observations or absent licensing yield no usable observation.
Imports reject ambiguous managed-device mappings. Only explicit compliant/noncompliant
observations map to true/false; other states remain unknown. Observed time comes from
lastSyncDateTime. Policy assignment and policy status rows cannot satisfy a compliant-device
grant. The later Conditional Access slice will consume this helper; this slice does not
claim that enforcement is already implemented.

## Queries and lifecycle

All listed collections/items support `$select` of declared fields, including supported
derived fields. Responses retain id and the type discriminator. Collections support
`$top` 1..1000 (default100), equality filters on applicable id/displayName/deviceName/
operatingSystem/userId/azureADDeviceId/complianceState/status fields, and asc/desc sorting
on applicable id/displayName/deviceName/lastSyncDateTime/lastReportedDateTime fields.
Unlisted query syntax, `$expand`, `$count`, `$search` and client `$skip` fail visibly.

Collections use an 8 MiB page budget and one-hour opaque continuations bound to tenant,
client, subject, permissions and route. Metadata and relationship revisions stay stable
across import, maintenance and PostgreSQL/API restart; every page rechecks current
identity and licensing. Reset returns410 for old handles even before fixture restoration.
All aliases share the deviceManagement budget, including injected429/Retry-After and
supported read-only batch requests. The existing encoded-path batch restriction remains.

Shared JSON/NDJSON/ZIP export, reset, restore and bounded maintenance apply. Evidence
archives exclude license inventory: explicitly provision licensing before restoring.
Migration048 preserves prior data, adds the device-mapping index and budget bucket, and
refuses a downgrade that would leave Intune evidence/budgets behind.

## Original client qualification

[intune-manifest.json](../intune-manifest.json) pins the original Python SDK and
Microsoft.Graph.DeviceManagement / Microsoft.Graph.Devices.CorporateManagement modules.
`python -m tools.verify_intune` creates an isolated local deployment and collects101
devices through root, user and detected-app paths, all eight typed policies, eight
assignments and108 status observations. Both clients resume saved pages after an import
and PostgreSQL/API restart while current reads expose the new observation. Qualification
is a required CI gate; this document describes the harness, not a blanket certification
of every Intune operation.
