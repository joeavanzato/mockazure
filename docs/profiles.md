# Composable population profiles

Profiles are bounded JSON or YAML data. The installed schema is
[`profiles/models.py`](../src/mockazure/profiles/models.py); the console and CLI use
the same parser, resolver, generator registry, service adapters and durable runner.
No profile executes Python, shell, arbitrary HTTP, filesystem includes or SQL.

For linked mail, calendar, project files and Teams history, see
[Connected business history](connected-content.md).

## Three document kinds

| Kind | Purpose | Fields beyond `schemaVersion`, `name`, `kind`, `extends` |
| --- | --- | --- |
| `tenant` | Baseline identities, users and entitlements | `parameterDefinitions`, `parameters`, `resources`, `steps` |
| `data` | Additive service data or evidence | Same as tenant |
| `template` | Select tenant baseline and ordered layers | `tenantProfile`, `dataProfiles`, `parameters` |

Each document has `schemaVersion: 1`. Names match `[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}`.
`extends` references a same-kind document in the bundle; cycles/missing parents fail.
Export the built-in bundle to start with the actual definitions:

```powershell
mockazure profile export --bundle-id builtin --output .runtime/my-profiles.json --config .runtime/instance/native.json
mockazure profile validate --bundle .runtime/my-profiles.json
```

The aggregate shape is `{"documents":{"profile-name":{...}}}`. A directory of
individual `.json`, `.yaml` or `.yml` documents is also accepted. An imported bundle
is complete and self-contained; include every referenced parent/data layer.

Add a template to the exported bundle without duplicating its data profiles:

```json
{
  "schemaVersion": 1,
  "name": "my-collection",
  "kind": "template",
  "extends": "collection-lab",
  "parameters": {"shared.users": 50, "shared.seed": 42}
}
```

A child template can replace the layer list explicitly:

```json
{
  "schemaVersion": 1,
  "name": "small-investigation",
  "kind": "template",
  "tenantProfile": "small-business",
  "dataProfiles": ["baseline-business-activity", "security-evidence", "suspicious-signins"],
  "parameters": {"shared.users": 25, "shared.days": 3}
}
```

## Precedence and dependency rules

1. Resolve tenant ancestry, then ordered data-profile ancestry. Shared parents apply
   once at first use.
2. Recursively merge maps; scalar values and lists replace. Lists never silently
   concatenate, so a child `dataProfiles` list is its complete list.
3. Duplicate resource/step logical keys require the later definition to include
   `override: true`. Missing dependencies, unknown operations and cycles fail.
4. Parameter values apply from defaults, profile layers, template ancestry, then
   explicit runtime overrides. Type and inherited bounds remain enforced.
5. Execute steps in dependency order. Named resource declarations hold property
   overlays and binding targets; they are not implicit resource-creation commands.

Non-shared parameters are qualified as `profile-name.parameter`; shared definitions
use `shared.parameter`. A definition has `type` (`integer`, `number`, `string` or
`boolean`), `default`, optional numeric `minimum`/`maximum`, and optional `shared`.
Common parameters are `shared.users`, `shared.days`, `shared.seed` and
`shared.referenceTime` (timezone-bearing ISO 8601). For example:

```json
{"shared.users": 500, "shared.days": 14, "volume-signins.records": 5000,
 "shared.referenceTime": "2026-10-07T00:00:00Z"}
```

References are typed single-key objects: `{"$param":"shared.users"}` and
`{"$ref":"primary-user"}`. They are not string interpolation. Service-created
resources bind their actual returned IDs, including workspace customer IDs and
message/secret identifiers; dependent steps use those bindings.

## Steps, generators and modes

A step requires `family`, `operation`, `mode`, `parameters` and optional `target`,
`dependsOn`, `override`. A simple example from the baseline is:

```json
{"users":{"family":"directory.users","operation":"import","mode":"fixture",
 "parameters":{"generator":"directory.users","count":{"$param":"shared.users"}},
 "dependsOn":[],"target":"primary-user"}}
```

`fixture` provisions validated historical/configuration state through existing
repositories. `service` calls the real local HTTP operation with a named identity,
normal OAuth/licensing/RBAC and response verification. The distinction is displayed
in catalog descriptors and previews. It does not bypass a public API's schema.

Packaged generators cover directory, evidence, mail, SharePoint/Teams, Intune,
Azure inventory and logs; packaged recipes connect permissions, content, Storage,
Key Vault and diagnostics. The exact registry is
[`generators/__init__.py`](../src/mockazure/population/generators/__init__.py), and
[`data/`](../src/mockazure/profiles/data) contains complete examples of each profile.
Use `GET /_mock/resources/catalog` or the Resources screen for operations, paths,
schemas, identity requirements and limitations. Read-only catalog families do not
claim a write operation.

## Replay, preview and versions

Stable fixture IDs derive from tenant ID, dataset namespace, logical key and seed.
The first implicit reference time is frozen for subsequent runs in that namespace.
Preview stores a resolved snapshot and tenant-state preconditions for one hour.
Apply checks them atomically; preview alone grants no write authority. Fixture
writes use serializable transactions; a concurrent change returns a conflict.
Public ARM, FileREST, Queue and Key Vault population writes use local
`X-MockAzure-If-None-Match: *` guards under existing domain locks. File upload
ranges also use `X-MockAzure-If-Match` with the creation ETag. These are optional
local management extensions, not Microsoft REST contract headers, and do not
grant permissions. Requests without them retain ordinary service semantics.

Imported/edited bundles have immutable versions. Runs retain the snapshot and
generator version they used. Repeating the same namespace rechecks recorded state;
drift or uncertain non-idempotent service responses require attention rather than
inventing success or blindly duplicating a POST. Use a new namespace for deliberately
different data. Updating generator behavior can invalidate an older run's resume
contract; keep the original application version when recovering old runs.

`populate manifest` exports redacted provenance/counts. `populate ground-truth`
streams JSON Lines with verified namespace receipts, public IDs and parents for
detection-test assertions. It is not a complete raw-state/credential export.
Synthetic Key Vault values and credentials are excluded from manifests/previews.

## Limits

Individual source documents: 1 MiB; bundle: 16 MiB, 128 documents, 100,000 parse
nodes, depth 32. YAML anchors, aliases, explicit tags, duplicate keys and merge keys
are rejected. A resolved run has at most 1,024 steps/resources. Generators produce
bounded batches; normal fixture worker batches are 100 records, service actions one
at a time. Resource browsing caps at 500 rows/API page (50 in the console); previews
include at most 10 samples per family. Content operations use existing chunked storage.

Console SharePoint/Teams/Intune edits budget at most 10,000 existing plus submitted
records and 20,000 existing plus submitted relationships per scope. The check runs
under the scope lock before loading its validation graph; conservative replacement
batches count against that budget too. Larger scopes remain browsable and require
the explicit bulk workload interface for edits.

Large imported or historical service datasets may need the existing bulk/import
interfaces rather than the console editor. The inspector remains a curated view,
not a general database administration tool. Shared-lab users have equivalent
management access; the console is not a multi-role administration product.

## Connected content and finite activity

The [living-tenant presets](living-tenant-profiles.md) add connected directory,
mail, collaboration, device and Azure data. [Finite activity](finite-activity.md)
adds fixed-count public actions with pacing, cancellation and safe resume.
