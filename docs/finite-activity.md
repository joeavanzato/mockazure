# Finite activity

Population can repeat one supported public-action recipe for a fixed number of
actions. It still runs the template's setup and historical fixture steps. It does
not start a continuous scheduler.

In the console, choose a template in **Profiles**, enable **Run a finite activity
batch**, and enter its public action step key, action count and optional pacing.
Preview validates the operation, dependencies and inputs before applying anything.
The Runs view shows verified actions, the data reference time and partial progress
after cancellation.

For example, `collection-lab` includes a `calendar` step:

```powershell
mockazure populate preview --template collection-lab --namespace meetings-01 `
  --tenant-id YOUR-LOCAL-TENANT-ID --activity-step calendar `
  --action-count 25 --pacing-ms 1000 --output preview.json --config PATH
mockazure populate apply --preview-id PREVIEW-ID --digest PREVIEW-DIGEST `
  --wait --config PATH
```

Use the corresponding `cancel`, `resume`, `status` and `manifest` commands with the
returned run ID. Limits are 1–100,000 actions and 0–60,000 milliseconds of pacing.
Large batches use one indexed step rather than expanding the profile into thousands
of steps. Pacing releases the worker lease and schedules its next opportunity.

Packaged recipes support calendar creation, Blob/File uploads, Table inserts,
Queue messages and synthetic secret creation. Repeated events have separate
transaction IDs; uploads and secrets receive separate names; Table entities receive
separate row keys. Uploaded bytes still come from the template's selected content
object. Other public-action families are usable only when the selected packaged
generator supplies their required payload and named identity; membership imports
do not grant Teams send permission.

The activity step must be a supported service operation with no resource target
binding and no dependent setup steps. Fixture imports, account provisioning and
destructive operations are not repeatable activity recipes. Invalid choices fail
during preview.

Resume uses the frozen recipe, generator version and reference time. It rechecks
acknowledged results and resumes remaining work. If a non-idempotent POST may have
succeeded but its response was lost, the run requires reconciliation; resume will
not blindly repeat that POST. Cancellation preserves committed actions.

The historical reference date controls generated data only. OAuth expiration,
sessions and effective permissions continue to use security time. Every action
authenticates against the local service and remains subject to its ordinary checks.
Reports contain safe progress metadata, not tokens, passwords or secret values.

The resource inspector links current SharePoint, Teams and Intune relationships.
Each link remains scoped to the selected tenant and workload; the detail view shows
up to 50 relationships. Fixture and public API operations retain separate labels.

## Qualification

```powershell
python -m tools.verify_living_tenant --config PATH --phase content `
  --report work/living-content.json --scale small --distribution native
```

The content gate populates an isolated tenant, compares IDs through the original
Graph SDK, downloads matching document bytes through the original Blob and File
SDKs, reads Table and Queue data, collects mailbox links through the original
Exchange module, and verifies namespace replay creates no duplicates. It writes
an incomplete report if any required check fails. The verifier supports
`--phase content|evidence|identity|hunting|all`; `all` runs each gate against its
own populated tenant. See the [combined qualification results](qualification/living-tenant-results.md)
for Native, Compose, installed-package, browser and large-preset evidence.

Python client dependencies come from the locked `clients` group. Exchange
qualification requires the unchanged, checksum-verified ExchangeOnlineManagement
3.11.0 package and dependencies under `work/living-clients`, matching
`integrations/manifest.json`. The CLI does not download or silently substitute
client versions.

On Windows, this gate uses the pinned Linux PowerShell client image for Exchange
only. A container-local TCP relay preserves end-to-end localhost TLS to the native
API; the process trusts the mounted lab CA. The API, worker and Python clients stay
native. This avoids installing a root certificate into Windows or changing the
Exchange module. The report records `isolated-linux-client` for that client runtime.

The [recorded content qualification](qualification/living-content.json) passed
with 20 users and mailboxes, all four Storage clients, equal Blob/File byte hashes,
and zero creations on replay. It records the tested worktree fingerprint separately
from its base commit; `source_dirty` means it tested the pending feature changes.
The accompanying acceptance run passed 896 unit/integration checks and the native
browser test exercised count/pacing, cancellation/resume, preset defaults and
related-resource navigation. Larger scale and the remaining program phases have
separate qualification gates, now recorded in the combined results linked above.
