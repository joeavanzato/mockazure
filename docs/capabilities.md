# MockAzure capabilities

The authoritative runtime discovery is authenticated GET /_mock/capabilities.
Operations below are implemented subsets; unsupported methods, fields, queries or
provider versions fail explicitly. All state and evidence are tenant-isolated in PostgreSQL.

| Surface | Stored state and queryable evidence | Contract |
| --- | --- | --- |
| Entra authentication | Applications, users, grants, roles, credentials, local OAuth flows, sessions, signing keys, managed identity | [Identity](identity.md) |
| Graph directory | Users/groups, membership/owners, typed filters, snapshots, user/group delta, read-only batch | [Directory](../integrations/contracts/graph-directory.md) |
| Entra evidence | Sign-ins, directory audits, risk detections/risky users; delayed publication and retention; runtime action evidence | [Evidence](../integrations/contracts/graph-evidence.md) |
| Graph security administration | Apps/SPNs/devices, credentials, grants, role assignments, method descriptors, registration reports, bounded Conditional Access enforcement | [Security](security-contracts.md) |
| Mail/Exchange | Mailbox configuration/statistics, inbox and sweep rules, historical message traces | [Mail](mail-contracts.md) |
| Graph UAL | Durable create/poll/download audit queries, permissions/operational roles, workload filters and retained results | [Mail and UAL](mail-contracts.md) |
| Azure management | Subscriptions/resource inventory, resource groups/tags, inherited RBAC, immutable Activity Logs with 100ns filtering/paging | [ARM](arm-contracts.md) |
| Administration | Tenant metadata; separate public directory/evidence import/export; maintenance, fault injection and quota controls | [Evidence](evidence.md), [maintenance](maintenance.md) |

A configured API surface is not automatically a qualified complete collector workflow.
[Compatibility](compatibility.md) records proven invocations and upstream limitations.

Physical MFA/SSPR, proprietary risk scoring, mail delivery, arbitrary Azure resource
provisioning and full Conditional Access are outside the supported simulation.
Full Intune, SharePoint/Teams content, eDiscovery and broad Defender APIs require
additional explicit resource contracts. Unsupported endpoints never return invented
empty success. Collection expansion records distinguish implemented evidence from
remaining coverage gaps.

Completed milestone descriptions and failed historical qualification attempts are
retained in [the archive](archive/m1-m6/README.md).

## Security collection evidence

Five additional v1.0 families—alerts, alerts_v2, incidents, secureScores and
secureScoreControlProfiles—have typed atomic imports, permission-checked list/get,
SQL filtering and durable snapshot paging. See [the exact profile](collection-evidence.md).
Legacy alerts are retained for historical collector compatibility after upstream retirement.
