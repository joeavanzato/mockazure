# Native Teams workflows

Import a team and its channels with the existing workload fixture API, then use
Graph v1.0 for membership and messages. Imported roster evidence alone grants no
access. An authorized native member POST can adopt an imported row, preserving
its ID while explicitly granting current membership.

Use an application with `TeamMember.ReadWrite.All` to add the initial team owner.
For independent private/shared channel membership use `ChannelMember.ReadWrite.All`
or resource-specific `ChannelMember.ReadWrite.Group` consent on that team.
Private-channel members must already belong to the team. Removing a team member
also removes their private-channel roster and access. Shared-channel membership
is independent. Changes that would remove the last authoritative owner fail
atomically.

Use a named delegated member with `ChannelMessage.Send` or `ChatMessage.Send`
for ordinary sends. `Teamwork.Migrate.All` does not authorize these workflows.
Only the author can edit, soft-delete or restore a message, while still a member.
The response uses the server's current identity and timestamps. Reference
attachments identify existing local drive URLs; MockAzure never fetches them.
Messages and replies have durable IDs and revision history.

The bounded delegated member-management policy requires an authoritative owner,
except for self-removal. This is a local policy choice, not a claim to reproduce
every configurable Microsoft Teams owner/member policy.
The Graph operation and permission tables are pinned in
`src/mockazure/living_tenant/data/operations.json`; consent defaults follow the
[Microsoft permission reference](https://learn.microsoft.com/en-us/graph/permissions-reference).

The population catalog exposes service-mode `create` on `teams.chat`,
`add-team`, `add-channel`, `add-chat` on `teams.conversationMember`, and
`send-channel`, `send-reply`, `send-chat` on `teams.chatMessage`.
Each requires a named identity, exact path variables and a Graph request body.
Responses bind server-assigned IDs and are checked through the public read API.
For example, a `send-channel` payload is:

```json
{
  "variables": {"team": "local-team-id", "channel": "19:local@thread.tacv2"},
  "body": {"body": {"contentType": "text", "content": "Review is ready"}}
}
```

Give the sender the corresponding read permission as well for population
readback. POST receipts are manual-reconciliation operations: an uncertain
outcome must not automatically resend a message.

For a named delegated actor, provision `identity.clients` with
`delegated_user_id` and one Graph delegated permission set. The selected user must
be an active imported user with no login credential. The fixture initializes a
random password and stores it encrypted with the application secret; manifests
and catalog responses contain neither. It refuses to replace an existing
credential. Reuse the same named actor for later actions.

This actor performs the existing local authorization-code flow with PKCE,
consent and current policy enforcement before its service requests. It does not
mint a token directly or simulate successful MFA. A policy requiring MFA or a
password change fails the automated login explicitly. Disabled users and
applications cannot continue sending. Named delegated actors currently support
Graph only.

No chat-member PATCH exists in the pinned v1.0 reference. Meeting/federated
chats, migration, policyViolation writes, hosted-content creation and
visibleHistoryStartDateTime are unsupported. These inputs fail explicitly.
Native facts are retained separately from fixture imports; downstream audit
projection is governed by the operation-to-evidence map.
