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

Table storage and import support do not by themselves imply support for a query
transport. Public query execution, permissions and language limits are listed in
COVERAGE.md as they are qualified.
