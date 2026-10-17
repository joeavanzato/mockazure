# Graph security collection evidence

MockAzure stores imported security evidence in PostgreSQL and exposes it through five
Graph **v1.0** read families, using the existing local OAuth and authorization system.

| Collection under /v1.0/security | Structural type | Read permission (application or delegated) |
| --- | --- | --- |
| alerts | microsoft.graph.alert | SecurityEvents.Read.All |
| alerts_v2 | microsoft.graph.security.alert | SecurityAlert.Read.All |
| incidents | microsoft.graph.security.incident | SecurityIncident.Read.All |
| secureScores | microsoft.graph.secureScore | SecurityEvents.Read.All |
| secureScoreControlProfiles | microsoft.graph.secureScoreControlProfile | SecurityEvents.Read.All |

Each accepts its corresponding ReadWrite permission for reads. Modern alerts and incidents
also require a supported current directory role for delegated users: Security Reader,
Security Operator, Security Administrator, Global Reader or Global Administrator.
SecurityEvents.Read.All alone cannot authorize alerts_v2.

**Legacy alerts are a historical compatibility surface.** Microsoft scheduled the old
API for retirement on October 15, 2026. Retaining it here supports pinned older collectors;
it is not a claim that the upstream endpoint remains available.

## Populate evidence

Authenticate with the instance's local **admin token**, then POST JSON to
/_mock/tenants/{tenant_id}/collections/{family}/import:

    {
      "records": [
        {
          "id": "alert-001",
          "title": "Synthetic suspicious sign-in",
          "status": "new",
          "severity": "high",
          "createdDateTime": "2026-10-01T00:00:00.1234567Z",
          "incidentId": "incident-001",
          "evidence": [
            {
              "@odata.type": "#microsoft.graph.security.userEvidence",
              "userAccount": {"userPrincipalName": "analyst@contoso.test"}
            }
          ]
        }
      ]
    }

For this example use family alerts_v2. The response contains revision and count.
Use a **Graph access token**, not the admin token, for subsequent Graph reads.

Imports accept 1–1000 records and at most 8 MiB per request. IDs are case-sensitive,
1–256 characters, start with a letter or digit, and otherwise allow letters, digits,
underscore, hyphen, dot, colon and equals. Duplicate IDs within a batch are rejected.
An existing ID is replaced as a complete resource at the new revision; omitted properties
are not merged. All records validate before any replacement is written.

Sparse resources are supported. Every supplied property is checked against the pinned
official CSDL, including enums and 48 derived alert-evidence types. Unknown properties,
wrong nested types, invalid IDs and foreign tenantId/azureTenantId values reject the batch.
Open dictionaries remain limited to declared properties. Original JSON timestamp strings
survive round trips; top-level query timestamps retain 100 ns precision.
Imports do not fetch URLs, generate detections or contact Microsoft.

## Read and query

All five collections support GET list and GET by ID. Lists support $filter, $top,
$skip, $count=true|false, and server-issued @odata.nextLink.

- Modern alerts filter assignedTo, classification, determination, createdDateTime,
  lastUpdateDateTime, severity, serviceSource and status.
- Incidents filter the same fields except serviceSource. $expand=alerts joins imported
  alerts_v2 records by incidentId at the same snapshot revision. Incident read access
  authorizes this relationship; no extra alert permission is required.
- Legacy alerts additionally support $select and a single $orderby scalar property.
  Legacy $skip is limited to 500; other collections permit up to 100,000.
- Scores, control profiles and legacy alerts filter declared top-level scalar properties.
  Legacy alerts also support vendorInformation/vendor and vendorInformation/provider.
- String/enum/boolean fields support eq, ne and in. Numeric and timestamp fields support
  eq, ne, gt, ge, lt and le; null supports equality. Boolean and/or and parentheses compose
  filters. Unsupported options, fields and operators return a Graph error.
- Default order is newest createdDateTime first, then ID; missing dates sort last.
  Control profiles sort by ID. Pages default to 100 and cap at 1000. In this local profile
  $top controls page size. Expansions exceeding 10,000 alerts return an explicit error.

A cursor expires after one hour and binds tenant, client, subject, token kind, permissions,
current delegated roles, resource family and advertised origin. Every page rechecks current
identity and authorization. Use the nextLink unchanged, without extra query options.
PostgreSQL persists the cursor and version snapshot, allowing another API instance to resume
while imports continue. Current reads see new versions; an older cursor sees its original
evidence. Indexes support tenant/family/ID, creation time and incident relationships.

This first profile retains imported versions and cursor rows; no automatic pruning, delete
API or reset integration is provided yet. Back up PostgreSQL with the instance keys. Generic
tenant exports remain metadata-only; they do not export this evidence store.

## Collection workflow coverage

[The generated inventory](../integrations/contracts/collection-coverage.json) parses the pinned
original Hawk and Microsoft Extractor Suite source. It records file hashes, named service
calls, parameters, URL templates, local helpers and unresolved dynamic call locations.
A mapped family is **not** certification that every parameter combination or entire collector
succeeds. No collector source is rewritten.

The new workflow qualification exercises real Graph Python and PowerShell SDK paging and
typed evidence, plus original MES Get-SecurityAlerts and Get-SecureScore CSV outputs.
The pinned MES alert function reads graphAuth.Type, but its original helper returns AuthType;
this invocation consequently selects legacy alerts. Modern alerts are tested separately
through the real SDKs. Permission checks are not weakened for collector behavior.

Priority remaining gaps include mailbox messages/attachments/content, device registeredUsers,
additional Exchange configuration/transport APIs, SharePoint/OneDrive content, provider-specific
Azure diagnostics, Defender hunting and Purview operations beyond current UAL reads.
This release does not claim complete Hawk, MES, Graph or Microsoft cloud coverage.

After downloading the pinned client/collector dependencies, run:

    uv run python -m tools.collector_coverage
    uv run python -m tools.verify_collections

The verifier builds a fresh ordinary Compose lab and locked Python client image. PowerShell
uses the pinned image and local module cache. Client containers share only the API's isolated
network namespace, trust the generated CA and cannot contact an upstream tenant. Reports and
CSV files stay under the unique ignored work/default-smoke-* directory; the stack stops
afterward. No host trust or DNS changes are needed.

## Contract sources

Structural source: microsoftgraph/msgraph-metadata revision
34b8e9c29032c82352a6fd7138be0a864a842bb2, schemas/v1.0-Prod.csdl, SHA-256
286cdc5d812b88bba9870b452d63d37aed925e6180541f20e034c949cd1816f0.
Permission identities come from the existing hash-pinned official permission reference.

Operation contracts:
[legacy alerts](https://learn.microsoft.com/en-us/graph/api/alert-list?view=graph-rest-1.0),
[alerts_v2](https://learn.microsoft.com/en-us/graph/api/security-list-alerts_v2?view=graph-rest-1.0),
[incidents](https://learn.microsoft.com/en-us/graph/api/security-list-incidents?view=graph-rest-1.0),
[secureScores](https://learn.microsoft.com/en-us/graph/api/security-list-securescores?view=graph-rest-1.0),
[secureScoreControlProfiles](https://learn.microsoft.com/en-us/graph/api/security-list-securescorecontrolprofiles?view=graph-rest-1.0).
