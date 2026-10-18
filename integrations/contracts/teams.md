# Teams and chat evidence contract

This is a bounded Microsoft Graph **v1.0** read profile over PostgreSQL. The
[operation manifest](teams.json) pins official REST sources, retrieval hashes and the
CSDL revision. Beta, message sending/edit/delete APIs, calls/meetings, federation,
app installation APIs, extra export APIs and Teams delta are unsupported.

## Local evidence and authority

`POST /_mock/tenants/{tenant}/workloads/teams/import` accepts the shared versioned
workload format: one canonical team or chat per scope, typed records, explicit edges,
optional stable `key` distinct from public `id`, expected revision and tombstones.
The combined batch limit is 1,000 entries / 8 MiB. Keys and scopes are bounded to
512 UTF-8 bytes. IDs can contain opaque punctuation but no slash or backslash.

Kinds are team, channel, chat, chatMessage, conversationMember (including the
pinned aadUserConversationMember derived type), and chatMessageHostedContent.
Relationships are team/channels, team/channel/chat members, channel/chat messages,
message replies, and message hostedContents. A record has one relationship parent;
replies belong to channel root messages. Imported edits and deletedDateTime values
are observed evidence, not fabricated successful public mutations. Structural
navigation is imported through edges, never nested response payloads. Replacing a
source record replaces its outgoing edges; preserve those edges explicitly.

The local-only `filesFolderReference` kind contains exactly id, driveId and itemId,
with a channel/filesFolder edge. Import resolves a real tenant-local SharePoint
folder; the public filesFolder response reads that canonical driveItem. It does not
copy its metadata. Restore SharePoint evidence before Teams references. No attachment
or web URL is fetched. Hosted content uses finalized shared binary uploads referenced
by content_id; metadata cannot supply contentBytes inline.

Authoritative access is separate: `POST .../workloads/teams/access` takes scope,
resource_kind (team/channel/chat), resource_id, principal_kind (user/application),
principal_id and permission. User membership uses permission `member`. Applications
use an allowlisted resource-specific consent permission on its team or chat.
`DELETE .../teams/access/{id}` revokes it. These controls model local installation
consent; they are not ordinary Entra app-role grants and have no invented Graph GUIDs.
An authenticated roleless application token can use an explicit current RSC grant.

Imported conversationMember rows never grant access. Delegated callers need explicit
team/chat membership, or channel membership for private/shared channels. Standard
channels also inherit their team's explicit user membership. Tenant-wide application
permissions do not impersonate a user. Teams administrator membership bypass, cross-tenant
membership and installed-app `.WhereInstalled` permission semantics are not modeled.
Operation permission sets are defined separately in team_access.py; notably chat-message
collection RSC does not authorize individual-message or hosted-content operations whose
pinned REST tables list only tenant-wide application permissions. Channel hosted-content
operations exclude legacy Group permission shortcuts. filesFolder has its own Files/
Sites/Group or File.Read.Group check; delegated callers also need local site access.

## Reads and queries

| Operation | Implemented query profile |
| --- | --- |
| Team GET; channel GET | `$select`; channel summary only when explicitly selected |
| `/me/joinedTeams`, `/users/{id-or-UPN}/joinedTeams` | No query options; local durable continuation if needed |
| Team channels | `$select`, equality filter on id/displayName/membershipType; visibility filtered before paging |
| Team/channel members | `$select`, `$top` 1..999 (default 100), equality filter on id/userId |
| `/chats`, `/me/chats`, `/users/{id-or-UPN}/chats` | `$top` 1..50; equality filter on id/chatType; descending lastMessagePreview/createdDateTime ordering |
| Chat GET and chat members | No query options |
| Channel messages | `$top` 1..50 (default 20), `$expand=replies`; newest root/reply modification first |
| Channel replies | `$top` 1..50; expanded replies initially up to 200, then nested continuation |
| Chat messages | `$top` 1..50; descending lastModifiedDateTime (default) or createdDateTime; matching date filter |
| Message GET, hosted-content metadata/raw `$value`, filesFolder | No query options |

Chat filters support modified gt/lt and created lt, with `and` combinations. As the
REST contract specifies, filters apply only with an explicit matching `$orderby`;
otherwise recognized date filters are ignored. Other filter syntax is rejected.
Unlisted `$expand` and query options fail visibly. Chat discovery and `/me` require
a delegated user; application discovery uses `/users/{user}/chats`. Delegated user
aliases are self-only in this profile. Chat item/message/hosted-content user aliases
use the same membership and operation checks as canonical routes.

Every collection has an 8 MiB response budget, server-controlled paging, and opaque
one-hour PostgreSQL continuations bound to actor, route and query. Metadata revisions
remain stable across imports/restarts; every page rechecks identity/authority. Scope
reset returns 410 for old continuations; changed membership/consent returns 403 and
requires a fresh collection. No Teams delta endpoint is invented. Hosted bytes stream
from the local content store while holding their read lease.

Shared Graph throttling and deterministic 429/Retry-After injection apply. Read-only
batch accepts qualified teams/chats roots; its existing restriction against encoded
path segments still applies. Use individual requests for encoded opaque IDs.

## Lifecycle and qualification

The shared JSON/NDJSON/ZIP export, reset and bounded history cleanup apply to Teams.
ZIP archives include typed metadata, byte hashes and exact hosted bytes. Restore
validates tenant/workload/scope and finalized local bytes, preserving stable record
keys. Evidence archives exclude authoritative memberships/consent; provision those
explicitly through the access control after fixture reset. Tokens/passwords are not
exported. Migration 047 preserves existing stores and refuses a downgrade that would
lose Teams state or its budgets.

The original Python SDK and Microsoft.Graph.Teams PowerShell module are pinned in
[teams-manifest.json](../teams-manifest.json). `python -m tools.verify_teams` provisions
a fresh isolated local deployment, discovers joined teams/channels/chats, collects
101 channel and 101 chat messages plus 201 replies through all pages, hashes hosted
content, restarts PostgreSQL/API, and resumes a saved snapshot while current reads
show an imported edit. Local qualification passed with both original clients; the final PR commit must also
pass the CI client and regression gates. Privacy transitions invalidate existing channel
continuations even when explicit membership records are unchanged.
