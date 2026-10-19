# Use MockAzure as a local Microsoft cloud lab

MockAzure runs local HTTPS APIs backed by PostgreSQL. Populate a tenant with reusable
profiles, browse it in the console, then point your application or collector at the
local endpoints. Microsoft resource audience strings remain unchanged; network
destinations must use your lab's endpoint profile and CA.

The console is at **https://localhost:8443/_mock/ui/** by default. It has one shared
lab password. That login authorizes lab management; applications still need their
own local OAuth permissions, licences and supported service roles.

## Deploy with Docker on Windows

Use Docker Desktop with Linux containers and PowerShell, from the repository root.
The image contains the application, built-in profiles and console; it needs no Node
server or Internet connection at runtime. Initial image building needs dependencies.

```powershell
docker build -t mockazure:local .
docker run --rm --network none --mount "type=bind,source=$($PWD.Path),target=/project" mockazure:local init --compose --project-dir /project
docker compose --env-file .runtime/compose.env up -d
docker compose --env-file .runtime/compose.env ps
docker compose --env-file .runtime/compose.env exec api mockazure console password set --config /instance/container.json
```

The password command prompts twice without echo. Use 12–1024 UTF-8 bytes. For
automation, `--password-file` reads a protected local file; avoid passwords in
command arguments. A password change revokes all browser sessions immediately.
There is no unauthenticated web setup route.

Initialization creates `.runtime/instance/native.json`, `container.json`, a lab CA,
server certificate, management token and database credential. Startup migrates
the database and initializes persistent OAuth keys before starting API/worker.
PostgreSQL uses a durable Docker volume; `docker compose stop` retains it.

For Git Bash, set `MSYS_NO_PATHCONV=1` for `docker run` and use `source=$(pwd -W)`.
Choose different ports during initialization with `--port 28443 --db-port 55434`.
The API base port reserves six following ports for data services. With a changed
published port/hostname, administrative CLI calls **inside** the API container use:

```powershell
docker compose --env-file .runtime/compose.env exec -e MOCKAZURE_ADMIN_BASE_URL=https://localhost:8443 api mockazure capabilities --config /instance/container.json
```

The worker has its own configured internal route to `api:8443` through `api:8449`.
Do not replace that route with the host's published ports.

### Trust the lab CA

For a browser on this Windows account, import **only the CA from this lab**:

```powershell
Import-Certificate -FilePath .runtime/instance/ca.pem -CertStoreLocation Cert:\CurrentUser\Root
Start-Process 'https://localhost:8443/_mock/ui/'
```

Application clients can use `.runtime/instance/ca.pem` directly without installing
a system root. Do not disable certificate validation. Copy the CA certificate to
other callers; never copy `ca.key`, `server.key`, `identity.wrap` or `admin.token`
to ordinary application users. Remove the imported CA when retiring the lab.

## Populate from stacked profiles

A **tenant profile** establishes the baseline. Ordered **data profiles** add
connected evidence and resources. A **template** names that composition; child
profiles/templates inherit shared definitions. You do not need to copy a whole
tenant to create another testing variation.

| Template | Intended use | Default population |
| --- | --- | --- |
| `developer-lab` | Everyday Graph and collection development | 20 users; sign-ins, security, mail, SharePoint/Teams and Intune |
| `collection-lab` | Cross-service collectors | Developer data plus Azure inventory, Blob/File/Queue/Table data, Key Vault, logs/diagnostics and activity feed |
| `investigation-lab` | Detection and investigation examples | Collection composition plus suspicious sign-in fixtures |
| `volume-lab` | Pagination, recovery and bounded processing | 10,000 users and 100,000 sign-ins |

These are synthetic data recipes, not a malicious-activity execution engine.
Tenant baseline includes explicit Entra P2 SKU inventory for sign-in collection.
Per-user premium detail still follows the emulator's licence-assignment rules.

### Console workflow

1. Sign in, open **Profiles**, select a template and tenant profile.
2. Add/remove/reorder data layers. Choose an existing tenant or **Create a new tenant**.
3. Set a dataset namespace, user count, days and seed. Additional parameters use
   qualified names, for example `{"volume-signins.records": 2000}`.
4. **Preview population** shows counts, bounded samples and warnings without applying
   records. **Apply this preview** starts a durable run.
5. Watch **Runs** until it succeeds. Open **Resources**, select the tenant and service
   family, inspect a record or prepare an edit. Tables load 50 records at a time.

**Add data** uses the operation shown in the catalog. Fixture operations populate
validated state. Service operations call actual local public APIs with a named test
identity and ordinary permission checks. A family with no safe editor is explicitly
read-only. Existing records need a fresh preview and explicit replacement consent.
Upload content through the content control; downloaded content is an attachment.

The optional inspector is a curated, read-only scalar view across service tables.
Set `console_inspector_enabled: true` in the active API config and restart the API.
It excludes credential/session tables and arbitrary SQL or nested JSON projections.
Set `console_enabled: false` to disable the web console; the existing management
bearer API remains available.

### CLI and the thin script

Install Python 3.12+ and the package locally (`uv sync --locked` in a source checkout,
or install a built wheel with pip). The native config created by Docker points local
CLI commands at the published API and database ports.

```powershell
$cfg = '.runtime/instance/native.json'
uv run mockazure profile list --builtin
@{slug='demo'; display_name='Demo lab'; domain='demo.test'; licences=@('E5')} | ConvertTo-Json | Set-Content -Encoding utf8 .runtime/demo-tenant.json
@{'shared.users'=50; 'shared.seed'=42; 'shared.referenceTime'='2026-10-07T00:00:00Z'} | ConvertTo-Json | Set-Content -Encoding utf8 .runtime/demo-parameters.json
uv run python examples/populate.py preview --config $cfg --template collection-lab --create-tenant .runtime/demo-tenant.json --namespace demo-v1 --parameters .runtime/demo-parameters.json --output .runtime/demo-preview.json
$preview = Get-Content .runtime/demo-preview.json -Raw | ConvertFrom-Json
uv run python examples/populate.py apply --config $cfg --preview-id $preview.id --digest $preview.digest --wait --timeout 3600 --output .runtime/demo-run.json
$run = Get-Content .runtime/demo-run.json -Raw | ConvertFrom-Json
uv run mockazure populate manifest --config $cfg --run-id $run.id --output .runtime/demo-manifest.json
uv run mockazure populate ground-truth --config $cfg --run-id $run.id --output .runtime/demo-ground-truth.jsonl
```

`examples/populate.py` delegates to the packaged `mockazure populate` command;
it does not contain a second generator. `--bundle PATH` accepts your own composition.
Output files refuse overwrite. To repeat, generate a **new preview** targeting
`--tenant-id $run.tenant_id`, with the same namespace, template and parameters,
then apply it. Verified unchanged operations produce no duplicates.

A changed namespace intentionally creates a distinct dataset; shared tenant setup
resources can remain unchanged. Changed content in an existing namespace is a
conflict, not permission to replace existing state. Implicit reference time freezes
on the first namespace run; specify it explicitly for reproducible templates.
See [profile authoring](profiles.md) for inheritance, ordering and limits.

## Query the populated public APIs

Use [identity setup](identity.md) to create a local application, issue its secret to
a protected file and grant `Directory.Read.All` / `AuditLog.Read.All` as needed.
The generated profile identities stay encrypted on the server; they are not printed
in the console or manifests. A named identity can run console public actions without
revealing its credentials. Keep an application's OAuth credentials separate from
the shared lab password and administrative bearer token.

Send client credentials to `https://localhost:8443/{tenant-id}/oauth2/v2.0/token`
with `scope=https://graph.microsoft.com/.default`; then use the returned access
token for `GET https://localhost:8443/v1.0/users` or
`GET https://localhost:8443/v1.0/auditLogs/signIns`. Follow local `@odata.nextLink`
values. Trust the lab CA on both requests. The Microsoft scope is an audience,
not a network destination. Other services use the explicit endpoint profile and
their own permissions/RBAC. [COVERAGE.md](../COVERAGE.md) lists supported methods,
Microsoft references and material limits.

## Recovery and safe operation

```powershell
uv run mockazure populate status --config $cfg --run-id $run.id
uv run mockazure populate cancel --config $cfg --run-id $run.id
uv run mockazure populate resume --config $cfg --run-id $run.id --wait --timeout 3600
```

Cancellation takes effect at a durable checkpoint; applied data remains. Resume
rechecks receipts before continuing. A lost response to a non-idempotent operation
can become `needs-attention`: inspect the resource and run error before reconciling;
the runner will not blindly duplicate it. A stale/expired preview requires another
preview. One active population run per tenant is allowed; different tenants can run
independently. A worker must be running to publish delayed evidence and advance jobs.

There is no automatic rollback or namespace-wide delete. Use a disposable tenant
for isolated experiments and the documented service/tenant maintenance operations
for reset. Never delete the database volume as a routine rerun step. Back up the
database **and** the instance directory together; `identity.wrap` protects OAuth,
named profile identities and durable execution snapshots. Manifests/ground truth
are test evidence, not complete backups. See [storage/recovery](default-runtime.md)
and [maintenance](maintenance.md).

## Native and shared-host deployments

For native use, set `MOCKAZURE_DATABASE_URL` to your local PostgreSQL connection
URL, then run `mockazure init`, `mockazure db upgrade`, `mockazure identity initialize`,
and `mockazure console password set`. Run `mockazure serve` and `mockazure worker`
in separate terminals with the same `--config`. The API defaults to loopback.

For a **new** shared Docker lab, initialize with
`--bind-address 0.0.0.0 --advertised-host lab.mockazure.test` (plus any chosen ports),
then start Compose. Native `init` accepts the same options. Arrange local DNS/hosts
resolution for that hostname on every caller and allow the seven service ports
through the host firewall. PostgreSQL publication stays at `127.0.0.1`. The generated
certificate includes the advertised hostname. Use that exact origin for the console;
it enforces Origin/CSRF checks. `console_public_origin` can explicitly select a
configured local HTTPS origin when needed.

Reinitialization refuses divergent settings. For an existing host change, edit
the relevant endpoint profiles/allowed hosts and published bindings deliberately,
then run `mockazure cert renew --config CONFIG` and restart. Preserve the CA and
database credentials. Existing Compose installations should rerun `init --compose`
with their original arguments to add the internal worker endpoint and certificate
SAN safely. A port/SAN mismatch is a configuration problem, not a reason to bypass TLS.

Common failures: 401 means credentials/session; 403 means permissions, licences,
roles or Origin/CSRF; stale preview means state changed; pending jobs usually mean
the worker is stopped; TLS errors mean hostname/CA mismatch. Check API/worker logs
and the run's structured errors before retrying.

MockAzure does not execute guest compute, deliver real mail, reproduce proprietary
detections or implement SMB/NFS. See [qualification results](lab-console-results.md)
for measured workflows and remaining limits.
