# Evidence simulation

MockAzure publishes persistent sign-ins, directory audits, risky users and risk detections.
Microsoft Graph v1.0 and beta routes have separate contracts. The implementation validates
payloads against extracted Microsoft CSDL pinned in
[the schema reference](../src/mockazure/providers/graph/schemas/README.md).
The [operation contract](../integrations/contracts/graph-evidence.md) defines permissions,
roles, licence requirements, query subsets and declared simulation choices.

Native identity and directory operations capture immutable facts in the same transaction
as their state changes. Fenced background jobs publish provider records. Evidence has its
own generation, revision, retention and continuations; directory delta state is independent.
Risk levels are declared observations, not an implementation of Microsoft's proprietary
risk scoring. Administrative confirmation and dismissal persist overlays without rewriting
the original observation.

## Administration

All commands use the authenticated management HTTPS API and the configured instance CA.
Supply `--tenant-id UUID --config PATH` to each command below.

- `mockazure evidence state` returns the generation required for reset.
- `mockazure evidence settings [--input settings.json]` reads or patches settings.
- `mockazure evidence clock --seconds 60` advances a fixed evidence clock.
- `mockazure evidence import --input evidence.json` imports a historical dataset.
- `mockazure evidence export --output new.json [--family signIn] [--from TIME] [--to TIME]`
  streams a pinned snapshot to a new file.
- `mockazure evidence observe-risk --input observation.json` records a typed observation.
- `mockazure evidence drain --max-jobs 100` runs a bounded number of tenant evidence jobs.
- `mockazure evidence jobs [--state failed] [--limit 50] [--continuation HANDLE]` lists
  sanitized jobs; `retry --job-id UUID` retries a failed current-generation projection.
- `mockazure evidence cleanup --max-rows 500` performs bounded physical cleanup.
- `mockazure evidence reset --expected-generation UUID` retires this tenant's evidence.

Settings: delay_seconds 0–3600 (default 5), retention_days 1–3650 (default 30),
clock_mode wall/fixed, and fixed_time for fixed mode. Clock advancement never rewinds.
Continuations expire after one wall-clock hour even when evidence time is fixed.

Inventory uses `mockazure licensing sku put --sku-id UUID --file inventory.json` and
`licensing sku list`. Managed identities use `managed-identity create` and `enable`;
host bindings use `managed-identity host create/update/rotate`. Host create/rotate require
`--output NEW_PRIVATE_FILE`; one-time host headers are excluded from stdout and datasets.
The existing flat command spellings remain available.

## Historical datasets

The envelope contains schema_version 1, provenance, and records. Provenance supports
source, description, seed and generator. Each record contains family, api_version
(v1.0 by default), payload, occurred_at, available_at, origin historical, and optionally
entitlement_snapshot P1/P2, record-specific provenance and a risk-detection sign_in_id.
Times must include a timezone. Availability cannot precede occurrence. IDs and explicit
cross-links are validated; known foreign-tenant subjects are rejected. Absent subjects
are historical references and never create directory users.

Imports are atomic, limited to 10 MiB and 10,000 records. Identical content is idempotent;
conflicting IDs return 409. Unknown fields, secret-bearing fields and arbitrary nested
provenance are rejected. Audit differences are restricted to the supported public
directory and risk properties. Export preserves IDs, public payloads, provenance and
explicit sign-in links; it excludes internal authentication facts and credentials.

Exports pin a publication revision and evidence time and fetch at most 500 rows per query.
Concurrent mutations and cleanup do not change the snapshot. Disconnect releases the pin;
if storage is unavailable, the pin expires after one wall-clock hour. Export files can
exceed the single-import limit; split them into valid envelopes for re-import.

Reset changes only the evidence generation. Directory objects, licences, authentication
state and directory delta continuations remain intact. Old workers cannot publish into
the new generation. Prior sources are logically suppressed immediately and physically
retired in bounded cleanup calls. Sources, receipts and action cutoffs remain as replay
barriers, so importing an old identical source after cleanup or reset cannot resurrect it.
New live operations and new historical IDs can publish normally.

Directory dataset version 1 deliberately omits usageLocation, assignedLicenses and
assignedPlans. Neither directory nor evidence datasets constitute a full instance backup.
