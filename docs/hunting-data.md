# Local hunting data

The catalog contains the 20 approved Defender hunting tables. Their full column
definitions come from hash-pinned Microsoft Learn pages, including embedded source
commit IDs and per-column preview/conditional notes. The publishing repositories
are not public, so the published HTML is retained for offline verification.

```powershell
python -m tools.acquire_hunting_contracts --verify-only
```

`GET /_mock/tenants/{tenant}/hunting/tables` describes those local table contracts.
These management endpoints require the lab's existing authentication and are not
Microsoft Graph APIs. The service browser also exposes each `hunting.{TableName}`
family. Listing pages show source identity, timestamp and provenance; selecting a
row shows its typed record.

Import fixtures with
`POST /_mock/tenants/{tenant}/hunting/tables/{table}/import`:

```json
{
  "source_ids": ["example-device-observation-1"],
  "rows": [
    {
      "Timestamp": "2026-10-07T00:00:00Z",
      "DeviceId": "synthetic-defender-device-1",
      "DeviceName": "workstation-1.local.test"
    }
  ]
}
```

The server assigns fixture provenance. Input cannot claim native activity.
Unknown columns, incorrect types, missing/timezone-less timestamps, duplicate source
IDs and conflicting replays are rejected atomically. Identical replay inserts zero
rows. Omitted nullable columns stay omitted; no sensor observations are invented.
Maximums are 10,000 rows / 8 MiB per direct import, and 1 MiB per row. Generic
service-browser apply retains its existing 1,000-mutation limit.

Export with
`GET /_mock/tenants/{tenant}/hunting/tables/{table}/export?limit=100`.
Follow `continuation`; each page is tenant/table bound. Maximum 500 rows / 8 MiB
per page; lower the requested limit if large records exceed the byte bound.
Exported `properties.record` and `id` can be reimported as `rows` and `source_ids`.
An import of previously native history is still a fixture import. Internal provenance
metadata never appears as invented columns in public Graph hunting results.

Retention is explicit: POST `{"before":"2026-10-01T00:00:00Z"}` to
`/_mock/tenants/{tenant}/hunting/retention` removes older rows in that tenant only.
POST `{"confirm_tenant_id":"<same tenant UUID>"}` to the `hunting/reset` endpoint
removes all of its hunting rows. These authenticated destructive controls preserve
other service evidence and other tenants. They do not run automatically.

## Graph queries

POST `/v1.0/security/runHuntingQuery` with a Graph-audience token carrying
`ThreatHunting.Read.All`. Both application and delegated tokens are supported.
The generated SDK's `query`/`timespan` and the REST documentation's
`Query`/`Timespan` spelling are accepted; duplicate spellings and other properties
are rejected. The namespaced Graph action alias is also supported.

```json
{"Query":"DeviceInfo | project Timestamp, DeviceId, DeviceName | take 20","Timespan":"P7D"}
```

The response has Graph `schema` and `results`, with documented column names and
typed values. It has no Logs `tables` envelope, statistics, silent truncation, or
invented continuation link. The legacy Defender hunting transport is unsupported.

Before loading any table, the server checks the current identity, token permissions
and every input table's local service entitlement, including both sides of a join.
Configure those entitlements explicitly:

```http
PUT /_mock/tenants/{tenant}/hunting/settings
{"entitlements":["identity","email","endpoint","cloud-apps","alerts"]}
```

These switches represent local service availability, not comprehensive Microsoft
license compliance. Delegated users need an effective Global Administrator, Global
Reader, Security Administrator or Security Reader role, or an explicit local
View Data profile. PIM expiry and disabled/deleted identities apply at security
wall time independently of the lab evidence clock.

`PUT /_mock/tenants/{tenant}/hunting/visibility/{user}` sets
`{"view_data":true,"tables":["DeviceInfo","DeviceEvents"],"device_ids":["device-id"]}`.
An explicit profile overrides the role default. Every requested table must be
allowed; device IDs constrain all `Device*` input rows before evaluation.
`device_ids:null` permits all devices; `[]` permits none. Application tokens use
tenant-wide table visibility. This bounded profile is not full Defender unified
RBAC. User deletion removes this grant; restoration never restores it.

Only a tenant-local primary hunting workspace exists. A foreign or unknown
`workspaceId` falls back to that local primary; it never opens another tenant,
an arbitrary Log Analytics workspace, or ARM data. Settings GET reports the
synthetic primary workspace ID.

Queries use a single captured lab evidence time for `now()` and default intervals.
`Timespan` accepts the shared duration/ISO interval subset and intersects query
filters. Default native retention is 30 days. The explicit `sentinel-ueba`
entitlement permits a bounded 366-day window and the conditional IdentityInfo
UEBA columns; it does not add tables or implement a Sentinel workspace. Hidden
columns and unknown columns fail validation even when a table is empty.

The shared bounded KQL subset supports where/project/extend, summarize/count,
order/take, distinct, let, union, equality joins and mv-expand. Unsupported
operators/functions fail explicitly. Limits are 30 seconds, 100,000 output rows,
64 MiB accounted values and 1,000,000 intermediate/input rows. Exhaustion returns
an explicit query error. This is an emulator language profile, not complete KQL.

Pinned references: [Graph hunting action](https://learn.microsoft.com/en-us/graph/api/security-security-runhuntingquery?view=graph-rest-1.0),
[hunting access overview](https://learn.microsoft.com/en-us/defender-xdr/advanced-hunting-overview).
The legacy API reference is retained solely as a source for delegated data/device
scope; its transport and token-permission names are not reused.

## Connected telemetry profiles

`business-security-telemetry` stacks over the business mail/device layers in the
living templates. The small preset supplies 20 rows in every table; the enterprise
preset supplies 5,000 in each (100,000 total hunting fixture rows). Set
`business-security-telemetry.rowsPerTable` to change this independently of users
and bounded collaboration content. The layer provisions a named `hunting-client`
with Graph hunting/read-alert permissions and explicit local service entitlements.

Identity account IDs resolve to the same business users. Defender device IDs are
separate from Graph device and Intune IDs; `AadDeviceId` links the device inventory.
Network message IDs are distinct from internet-message IDs. Repeated telemetry
observations reference the retained mail corpus; attachment/file hashes are rendered
from its exact deterministic document specifications. AlertInfo and AlertEvidence
refer to imported, read-only Graph alerts and their user evidence.

All process, logon, network, registry, click, remediation and detector history in
these generators is fixture data. No process runs, network connection occurs or
detector evaluates the documents. Identity/device inventory is also labeled as a
fixture snapshot. Optional columns with no modeled observation are omitted.

Optional `hunting-suspicious-consent`, `hunting-mailbox-forwarding`,
`hunting-unusual-download`, `hunting-privilege-change` and
`hunting-unusual-secret-access` layers add explicitly labeled fixture cloud events.
They do not perform those operations or install permissions/forwarding rules.
Account targets resolve to business identities; an application or secret target
that was not observed stays omitted. Stack these layers onto a living template;
ordinary business defaults do not include them.

Native publication currently maps supported Exchange and SharePoint/Teams audit
outcomes to CloudAppEvents. It copies the validated action, account/object IDs and
sanitized raw audit from the same immutable source. It does not turn Graph reads,
Entra sign-ins, mail sends or imports into host/identity sensor, click, malware,
or alert records. See each table's `native_mappings` and each operation's evidence
disposition for the exact boundary.

Native rows share the existing generation, worker lease and atomic projection
receipt. Their source availability delay applies to public queries independently
of event time. Retrying a committed projection does not duplicate rows, including
after hunting retention/reset. Previously published sources are not backfilled
just because a newer native map exists. Importing exported rows creates fixture
attribution and does not enqueue native evidence. Population ground-truth exports
include hunting source IDs and provenance; public Graph rows contain no invented
provenance columns.
