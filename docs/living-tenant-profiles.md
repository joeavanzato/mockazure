# Connected business profiles

The `living-small-business`, `living-department` and `living-enterprise` templates
stack the same reusable layers. Their defaults are 20, 250 and 2,500 users with
7, 14 and 30 days of historical content. These are dataset presets, not measured
performance guarantees. Override `shared.users`, `shared.days`, `shared.projects`
and individual layer counts in the existing population preview workflow.

| Layer | Data and behavior |
| --- | --- |
| `organization-projects` | Deterministic users and project groups |
| `business-mail-calendar` | Mailboxes, conversations, MIME, attachments and meetings |
| `business-sharepoint-teams` | Sites, libraries, documents, versions, teams, channels and chats |
| `business-device-estate` | Owned directory devices, matching Intune devices, software and observed configuration/compliance status |
| `business-azure-operations` | Owned applications, linked compute/network inventory and business documents in Blob and File storage |
| `storage-content`, `keyvault-secrets`, `logs-and-diagnostics` | Operational account, four storage data planes, synthetic secrets, workspace and diagnostics setup |

Use a fresh dataset namespace for an independent estate. Reusing the same namespace
reconciles recorded results; changing its inputs conflicts rather than silently
retargeting existing resources. Server-returned service identities are saved in
bindings. Directory device object IDs differ from `deviceId`; Intune
`azureADDeviceId` links to the latter.

The Azure inventory defaults to eight resources (one connected network/compute
set). `business-azure-operations.inventoryResources` can select up to eight per
project. Nested ARM resource names include their parent names. The owner object
IDs in resource tags identify real generated users, but tags do not grant access.
The project application and service principal have actual directory owner links.

Historical records are explicit fixtures. Intune observations neither enroll
devices nor execute policies. Ordinary directory devices are not marked compliant
merely because an Intune observation says so. Compute/network inventory does not
deploy a machine; use provider operations when testing operational behavior.

Storage, Key Vault, workspace and diagnostic setup uses normal local public APIs
with a named application and explicit role assignments. Blob and File uploads
contain the same generated Office document bytes. Queue messages and table entities
are readable through their respective APIs. Diagnostic settings point to the actual
created workspace. Synthetic secret values remain behind authenticated Key Vault
access; manifests and version-list responses contain no secret values.

Content volumes remain separate from user counts. For example, project and document
counts can stay small while directory and device counts grow. All templates use the
same base layers, so changing a shared layer does not require copying template data.
