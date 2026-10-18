# Management Activity pull-feed contract

Canonical Microsoft base: `https://manage.office.com/api/v1.0/{tenant}/activity/feed`.
Send every request to the configured local `activity` origin. The canonical URL selects
the token audience; it never causes forwarding. Old endpoint profiles default `activity`
to their configured login origin. Host/container profiles export the new field.

Pinned sources and permission identities: [operation manifest](activity-feed.json).
Common record enum values come from the pinned Graph security CSDL numeric definitions;
common required fields follow the Management API schema. These are distinct service wrappers.

| Operation | Implemented profile |
| --- | --- |
| POST subscriptions/start?contentType=... | Idempotent enabling per tenant/client/type; empty body or `{}`. Webhook configuration rejected. |
| POST subscriptions/stop?contentType=... | Disable own subscription; empty 200 response. Future retrieval of previously discovered content fails. |
| GET subscriptions/list | Raw array with contentType/status/webhook:null for this client. |
| GET subscriptions/content?contentType=... | Publication-time window, descriptor array, opaque durable `NextPageUri` response header. |
| GET audit/{contentId} | Immutable JSON audit array. Original event `Id`, no Graph wrapper. |

All five types: Audit.AzureActiveDirectory, Audit.Exchange, Audit.SharePoint,
Audit.General, DLP.All. One audit event per local content blob is intentional; it remains
an array. Exchange, SharePoint/OneDrive, and Entra service names select their feed;
other services use General. Pinned DLP record types select DLP.All before workload mapping.

Authorization uses actual signed local **application** tokens with ActivityFeed.Read;
DLP operations additionally require ActivityFeed.ReadDlp. Graph tokens fail. Tenant path,
current application/service principal, subscription state, DLP authority and content
ownership are checked on every request. Delegated access is explicitly unsupported in
this profile; no delegated permission GUIDs are invented. Subscriptions belong to the
client application, not the publisher identifier. PublisherIdentifier is an optional
vendor tenant GUID; absent IDs share the default quota, and clients using the same
publisher share a tenant-local quota. Quotas return AF429 with Retry-After.

Windows use publication time, not CreationTime: both startTime/endTime or neither,
positive and at most 24 hours, start no older than seven days. Omitted bounds select
last 24 hours. Start is inclusive and end exclusive. UTC date/timestamp strings accepted;
other offsets, duplicate or unknown query parameters rejected. Content expires seven
days after publication. A local clock can make event/publication tests deterministic;
tokens, continuation expiry and quotas use wall time. Pagination snapshots exclude later
publications; cursors bind tenant/client, exact permission set, type, publisher and
subscription generation and expire after one hour. Stopping/restarting invalidates old
pages and excludes content published before the new subscription start revision.

## Publication and lifecycle

The first subscription or admin configuration activates native publication. Subsequent
canonical auditLogRecord publication enqueues a separate leased job in the same database
transaction. The outbox copies immutable public facts, so Graph retention cannot erase a
pending job. Common Graph metadata overrides duplicate dynamic common fields; public
workload extension data survives. Missing required common data fails that feed job visibly
without preventing the canonical Graph observation. No pre-activation automatic backfill.
Admin publish can explicitly publish existing canonical record IDs. Original event IDs
correlate with Graph UAL; local content IDs are generation-scoped and opaque.

All controls below require the separate MockAzure admin token under
`/_mock/tenants/{tenant}/activity`:

| Control | Contract |
| --- | --- |
| GET state | Generation/revision/settings and publication-job counts. |
| PATCH settings | delay_seconds 0..86400; fail_attempts 0..2 per job; page_size 1..1000; request_limit 1..100000; window_seconds 1..3600. |
| POST publish | `{record_ids:[UUID,...]}`: canonical auditLogRecord references, at most 1000. |
| POST import | `{records:[common-audit-record,...]}`: feed-only fixtures, at most 1000 / 10 MiB request, 1 MiB per record. |
| POST drain | `{max_rows:100}`: run currently due leased jobs, at most 1000. Worker service also handles these jobs normally. |
| GET export?after_revision=N | Bounded JSON page with settings/contents/next_revision, at most 1000 records / 8 MiB. Concatenate content pages for larger datasets. No tokens or subscription authorities. |
| POST restore | Import one exported page atomically into the same tenant; preserve event IDs/publication times. Does not restore subscriptions/grants. New content IDs follow current generation. Subscribe before restoring for visibility. |
| POST reset | expected_generation required. Clears feed contents/subscriptions/cursors/quotas/jobs and rotates generation. Leaves canonical Graph evidence intact. Optional clear_settings also clears feed configuration. |
| POST cleanup | max_rows bounded pruning of expired payloads and cursors. Permanent identity/hash receipts prevent retries resurrecting expired content; reset clears those receipts. |

Publication retries are idempotent; conflicting content with an existing event identity
fails atomically. Reset fences running workers. Expired payload pruning retains small
identity receipts until reset. Settings and feed records are included in database backups;
page exports are portable evidence, not identity/security backups.

Explicit gaps: webhook delivery/notification history, delegated feed auth, Service
Communications/ServiceHealth APIs, automatic pre-subscription backfill, cloud-specific
national endpoints, full workload-specific semantic validation, inferred audit generation
for read-only SharePoint/Teams/Intune imports. The common schema is validated and public
workload extension fields are preserved; this does not claim every product schema rule.
Native Exchange/Graph mailbox mutations already produce canonical UAL and therefore feed
records. No Microsoft tenant or external link is contacted.

Qualification command: `python -m tools.verify_activity_feed`. It creates a fresh local
HTTPS/PostgreSQL lab, obtains tokens through the actual issuer, generates a native mailbox
rule audit, correlates its ID with Graph UAL, follows feed pages across database/API restart,
collects all five types and verifies stop revokes previously issued content URIs.
