# Hunting cookbook

Use the living business templates and their named hunting client. All examples use
`POST /v1.0/security/runHuntingQuery` with `ThreatHunting.Read.All`; the query is the
`Query` body property. Reference time follows the tenant evidence clock.
The console forwards the request with the selected test identity and displays the
actual evaluation time from the local `X-MockAzure-Reference-Time` response header.
This header is emulator metadata; the Graph schema/results body is unchanged.

Each example must return 1–20 rows with the indicated populated columns; count
columns must be positive. These assertions apply to populated living templates,
not empty tenants or restricted delegated profiles. Fixtures remain labeled in
the service browser, outside Graph row schemas. Joins deduplicate repeated
observations before joining to keep the large preset within intermediate limits.

## IdentityInfo — recent observations

```kusto
IdentityInfo | where Timestamp >= ago(30d) | project Timestamp, AccountObjectId | order by Timestamp desc | take 20
```

Expected populated columns: `AccountObjectId`.

## IdentityLogonEvents — recent observations

```kusto
IdentityLogonEvents | where Timestamp >= ago(30d) | project Timestamp, AccountObjectId | order by Timestamp desc | take 20
```

Expected populated columns: `AccountObjectId`.

## IdentityDirectoryEvents — recent observations

```kusto
IdentityDirectoryEvents | where Timestamp >= ago(30d) | project Timestamp, AccountObjectId | order by Timestamp desc | take 20
```

Expected populated columns: `AccountObjectId`.

## IdentityQueryEvents — recent observations

```kusto
IdentityQueryEvents | where Timestamp >= ago(30d) | project Timestamp, AccountObjectId | order by Timestamp desc | take 20
```

Expected populated columns: `AccountObjectId`.

## EmailEvents — sender with null fallback

```kusto
EmailEvents | project NetworkMessageId, Sender=coalesce(SenderDisplayName, 'unknown') | take 20
```

Expected populated columns: `NetworkMessageId`, `Sender`.

## EmailAttachmentInfo — recent observations

```kusto
EmailAttachmentInfo | where Timestamp >= ago(30d) | project Timestamp, NetworkMessageId | order by Timestamp desc | take 20
```

Expected populated columns: `NetworkMessageId`.

## EmailUrlInfo — recent observations

```kusto
EmailUrlInfo | where Timestamp >= ago(30d) | project Timestamp, NetworkMessageId | order by Timestamp desc | take 20
```

Expected populated columns: `NetworkMessageId`.

## EmailPostDeliveryEvents — recent observations

```kusto
EmailPostDeliveryEvents | where Timestamp >= ago(30d) | project Timestamp, NetworkMessageId | order by Timestamp desc | take 20
```

Expected populated columns: `NetworkMessageId`.

## UrlClickEvents — recent observations

```kusto
UrlClickEvents | where Timestamp >= ago(30d) | project Timestamp, NetworkMessageId | order by Timestamp desc | take 20
```

Expected populated columns: `NetworkMessageId`.

## DeviceInfo — recent observations

```kusto
DeviceInfo | where Timestamp >= ago(30d) | project Timestamp, DeviceId | order by Timestamp desc | take 20
```

Expected populated columns: `DeviceId`.

## DeviceNetworkInfo — recent observations

```kusto
DeviceNetworkInfo | where Timestamp >= ago(30d) | project Timestamp, DeviceId | order by Timestamp desc | take 20
```

Expected populated columns: `DeviceId`.

## DeviceLogonEvents — recent observations

```kusto
DeviceLogonEvents | where Timestamp >= ago(30d) | project Timestamp, DeviceId | order by Timestamp desc | take 20
```

Expected populated columns: `DeviceId`.

## DeviceProcessEvents — recent observations

```kusto
DeviceProcessEvents | where Timestamp >= ago(30d) | project Timestamp, AccountObjectId | order by Timestamp desc | take 20
```

Expected populated columns: `AccountObjectId`.

## DeviceNetworkEvents — recent observations

```kusto
DeviceNetworkEvents | where Timestamp >= ago(30d) | project Timestamp, DeviceId | order by Timestamp desc | take 20
```

Expected populated columns: `DeviceId`.

## DeviceFileEvents — recent observations

```kusto
DeviceFileEvents | where Timestamp >= ago(30d) | project Timestamp, DeviceId | order by Timestamp desc | take 20
```

Expected populated columns: `DeviceId`.

## DeviceRegistryEvents — recent observations

```kusto
DeviceRegistryEvents | where Timestamp >= ago(30d) | project Timestamp, DeviceId | order by Timestamp desc | take 20
```

Expected populated columns: `DeviceId`.

## DeviceEvents — count by action

```kusto
DeviceEvents | summarize EventCount=count() by ActionType | order by EventCount desc | take 20
```

Expected populated columns: `ActionType`. Positive counts: `EventCount`.

## CloudAppEvents — inspect dynamic fixture metadata

```kusto
CloudAppEvents | extend Simulation=tostring(AdditionalFields.simulation) | where Simulation == 'fixture' | project AccountObjectId, ActionType, Simulation | take 20
```

Expected populated columns: `AccountObjectId`, `ActionType`, `Simulation`.

## AlertInfo — recent observations

```kusto
AlertInfo | where Timestamp >= ago(30d) | project Timestamp, AlertId | order by Timestamp desc | take 20
```

Expected populated columns: `AlertId`.

## AlertEvidence — recent observations

```kusto
AlertEvidence | where Timestamp >= ago(30d) | project Timestamp, AccountObjectId | order by Timestamp desc | take 20
```

Expected populated columns: `AccountObjectId`.

## Join identity to device activity

```kusto
IdentityInfo | distinct AccountObjectId, AccountUpn | join kind=inner (DeviceProcessEvents | distinct AccountObjectId, DeviceId) on AccountObjectId | project AccountObjectId, AccountUpn, DeviceId | take 20
```

Expected populated columns: `AccountObjectId`, `DeviceId`.

## Join messages to document hashes

```kusto
EmailEvents | distinct NetworkMessageId, Subject | join kind=inner (EmailAttachmentInfo | distinct NetworkMessageId, SHA256) on NetworkMessageId | project NetworkMessageId, Subject, SHA256 | take 20
```

Expected populated columns: `NetworkMessageId`, `SHA256`.

## Join read-only alerts to entities

```kusto
AlertInfo | distinct AlertId, Title | join kind=inner (AlertEvidence | distinct AlertId, AccountObjectId) on AlertId | project AlertId, Title, AccountObjectId | take 20
```

Expected populated columns: `AlertId`, `AccountObjectId`.

## Join users to cloud activity

```kusto
IdentityInfo | distinct AccountObjectId, AccountUpn | join kind=inner (CloudAppEvents | distinct AccountObjectId, ActionType) on AccountObjectId | project AccountObjectId, AccountUpn, ActionType | take 20
```

Expected populated columns: `AccountObjectId`, `ActionType`.

The console renders at most 200 returned rows and offers complete JSON download.
Its forwarding transport caps responses at 16 MiB; use projection/aggregation/take
for larger datasets. Public Graph query engine limits remain documented in
[hunting data](hunting-data.md). No incident mutation or response action is supported.
