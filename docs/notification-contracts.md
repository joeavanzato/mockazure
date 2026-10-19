# Graph subscriptions and local receivers

Graph v1.0 operations and notification schemas use the pinned Microsoft references
in `integrations/contracts/cloud/graph-workflows.json`. Basic callbacks contain resource
identity metadata; rich encrypted resource-data notifications are explicitly unsupported.
No beta additions are advertised. Native four-ecosystem E qualification is still pending.

| Resource | Changes | Lifecycle |
| --- | --- | --- |
| `users/{id}/messages`, `users/{id}/mailFolders/{folder}/messages` | created, updated, deleted | reauthorizationRequired, missed, subscriptionRemoved |
| `users/{id}/events` | created, updated, deleted | reauthorizationRequired, missed, subscriptionRemoved |
| `drives/{id}/root` | updated | reauthorizationRequired |

`me` aliases require delegated authentication; delegated Outlook subscriptions target
the signed-in mailbox only. Application Outlook subscriptions require Mail.Read/ReadWrite
or Calendars.Read/ReadWrite. Delegated Mail.ReadBasic is accepted. Drive subscriptions
require Files.Read.All/ReadWrite.All and current site access. Only original-application
management is supported; advanced tenant-wide subscription inventory is not implemented.

Outlook maximum lifetime is 10080 minutes; drive maximum is 42300 minutes. Expiration
shorter than 45 minutes is raised to 45 minutes. Local quota is 1000 active subscriptions
per application. Creation validates each distinct callback URL by exact plaintext echo
of the URL-encoded validationToken. Updates can renew expiration or revalidate notificationUrl.

Configure `notification_receivers` in the instance JSON (or JSON environment override):

```json
{
  "notification_receivers": {
    "https://receiver.test:9444/notifications": {
      "address": "127.0.0.1",
      "ca_file": "receiver/ca.pem"
    }
  }
}
```

Named Docker receivers use their explicit private IP as address. Only registered exact
HTTPS URLs, private/loopback literal addresses and explicit trusted CA files are accepted.
Connections use the pinned address with the registered hostname for TLS verification;
callback DNS, proxies and redirects are not followed. An address/CA-path change requires
revalidation. The standalone `integrations/local_webhook_receiver.py` accepts --cert,
--key, --output, --host and --port and stores actual callback envelopes as NDJSON.
The output contains clientState and should be treated as local test application data.

Source writes commit outbox rows in the same transaction. Delivery rechecks current
identity/permissions, target generation, callback binding, subscription revision and
job lease. At-least-once delivery can duplicate after a crash. First attempts use a
three-second timeout, retries up to ten seconds, with exponential delays and a four-hour
deadline. A failed Outlook delivery at its deadline queues a missed lifecycle notification
when a valid lifecycle receiver remains. Slow/drop service-wide throttling heuristics are
not modeled. Current permission revocation, deletion, expiry or reset stops callbacks.

Use authenticated `POST /_mock/tenants/{tenant}/notifications/{subscription}/lifecycle`
with `{"event":"reauthorizationRequired"}` (or missed/subscriptionRemoved where supported)
to simulate service lifecycle events deterministically. Service removal immediately
retires data delivery. No undocumented Microsoft service token timer is invented.

Drive imports publish root changes only for affected trees. Calendar exception changes
publish the updated series master; clients retrieve calendarView/delta for occurrences.
These subscriptions do not add Graph drive writes or personal-folder notification support.

Cloud export contains subscription descriptions (application, resource, change types,
expiration), excluding callback URLs, clientState, actor authority and address bindings.
Import restores disabled descriptions and invalidates pending deliveries; explicitly
create subscriptions again after restore. Reset removes descriptions/outbox work.
Pruning removes old terminal delivery jobs and expired unreferenced subscriptions.
Migration 071 refuses a lossy downgrade until subscription state is reset.

Export is bounded to 1,000 subscription descriptions, including retained retired descriptions. Larger exports return an explicit 413 NotificationStateLimit; they never silently truncate. Prune eligible retired subscriptions before exporting.

Pending callbacks intersect their saved permission claims with current applicable application/delegated consent under the tenant security lock. Revoking a grant stops subsequent delivery even if its original public access token has not expired. New grants cannot expand a saved subscription actor beyond its original claims.
