# Connected business history

The built-in data layers `business-mail-calendar` and `business-sharepoint-teams`
share the `organization-projects` parent. They reuse the tenant profile and
explicitly override its user generation with the versioned `business-v1`
organization model. Use a fresh dataset namespace when switching an existing
dataset to these layers.

Add this template to an exported built-in bundle, then use the normal
[preview and population workflow](profiles.md):

```json
{
  "schemaVersion": 1,
  "name": "connected-history",
  "kind": "template",
  "tenantProfile": "small-business",
  "dataProfiles": ["business-mail-calendar", "business-sharepoint-teams"],
  "parameters": {
    "shared.users": 20,
    "shared.projects": 4,
    "shared.days": 7,
    "shared.seed": 42,
    "business-mail-calendar.messageCopies": 32,
    "business-mail-calendar.eventCopies": 8
  }
}
```

Mail counts are physical mailbox copies: two copies per message and four per
complete original/reply exchange. Calendar counts are organizer/attendee copies.
A single-user organization uses individual organizer events and one unique
conversation member per project.

| Layer | Data and relationships |
| --- | --- |
| Organization | Stable users with names, departments, locations and distinct IDs per namespace |
| Mail/calendar | Mailboxes, inbox/sent/draft folders, related sender and recipient copies, Internet headers, MIME, downloadable document attachments, recurring meetings and exceptions |
| SharePoint | Project sites, document libraries, root/project folders, current documents, previous versions, author references, permission evidence and separately declared site grants |
| Teams | Standard/private/shared channels, chats, observed members, threaded messages, mentions, document links and canonical channel file-folder references |

Document copies are rendered from the same input and revision, then stored in the
normal finalized content store. The mail attachment, MIME part and corresponding
drive revision have identical bytes. Formats include TXT, CSV, JSON, PDF, DOCX,
XLSX and PPTX; see the packaged corpus asset README for size and Unicode limits.
The generated URLs describe synthetic resources; they never trigger external
fetches. Use the emulated Graph endpoint for API collection and download.

These layers import history. They do not send messages, deliver invitations or
claim that a native service operation occurred. Imported Teams members do not
grant operational access. The `sharepoint.access` steps explicitly provision
site access; importing a `permission` evidence row alone does not.

The population runner records service identities under a namespace generation.
A retired generation cannot be resumed using stale mappings. Existing profile
generator behavior is preserved; each new business generator is independently
versioned in frozen run snapshots.
