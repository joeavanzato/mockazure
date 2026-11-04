# MockAzure

MockAzure is a local, stateful Microsoft cloud API emulator for evidence collection
and API testing. Python, PostgreSQL and real HTTPS back Entra authentication, Graph
directory/security/evidence APIs, Exchange collection commands and Azure management.

Use it with explicit local endpoint overrides and its generated CA. It never falls
back to a Microsoft tenant. Permissions, supported directory roles, policy conditions,
pagination and asynchronous jobs are enforced; unsupported operations fail explicitly.

## Populate and browse a lab

[Getting started](docs/getting-started.md) covers Docker/native deployment, the
shared-password console, stacked data templates, the population script and recovery.
[Profile authoring](docs/profiles.md) explains inheritance and reproducible datasets.
Open `https://localhost:8443/_mock/ui/` after setting the console password.

## Start on Windows

With Docker Desktop running in Linux-container mode, double-click
`start-mockazure.bat` or run it from a terminal. It builds the checkout, initializes
or validates the instance, starts the complete Compose stack, waits for readiness,
prompts for a console password when missing, and opens the web UI. No local Python
installation is required. The batch file uses `scripts/start-mockazure.ps1` beside it.

```powershell
.\start-mockazure.bat
# First initialization on a different port:
.\start-mockazure.bat -Port 28443
# Explicitly trust this lab CA for your Windows account:
.\start-mockazure.bat -TrustCa
```

Existing ports, certificates, credentials and database storage are reused. Omit
`-SkipBuild` after pulling changes; use it for faster starts when the image is
already current. `-SetPassword` explicitly resets the shared password. `-NoBrowser`
and `-NoPasswordPrompt` support service-only automation. `-Help` lists the options.
Certificate trust is changed only when you explicitly pass `-TrustCa`.

## Start with Docker (PowerShell)

Docker Desktop must use Linux containers. Run from this repository:

~~~powershell
docker build -t mockazure:local .
docker run --rm --network none --mount "type=bind,source=$($PWD.Path),target=/project" mockazure:local init --compose --project-dir /project
docker compose --env-file .runtime/compose.env up -d
docker compose --env-file .runtime/compose.env exec api mockazure capabilities --config /instance/container.json
docker compose --env-file .runtime/compose.env exec api mockazure tenant create --slug contoso --display-name "Contoso Lab" --domain contoso.test --config /instance/container.json
~~~

For Git Bash, set MSYS_NO_PATHCONV=1 on docker run and use source=$(pwd -W).
HTTPS defaults to https://localhost:8443 and PostgreSQL to 127.0.0.1:55432.
Initialization accepts --port and --db-port. For a changed published port, use
MOCKAZURE_ADMIN_BASE_URL=https://localhost:8443 for administrative calls inside api.

Fresh installations use a durable Docker-managed PostgreSQL volume. Configuration,
credentials and CA material live under .runtime/ and are not tracked. TCP readiness,
migration and persistent identity initialization complete before API/worker startup.

**Existing installation:** run initialization again before using the updated Compose
file. An existing .runtime/postgres database keeps its explicit bind mount. Missing
legacy database state causes initialization to stop; it is never silently replaced
with an empty volume. See [storage and recovery](docs/default-runtime.md).

## Compose files

Use `compose.yaml` to run the application and `compose.test.yaml` for the default
integration-test database. Additional release, milestone and test environments
live under [`deploy/compose/`](deploy/compose/README.md), with a guide explaining
which are current and which are retained for reproducibility. Most users need only
the two root files; the Windows launcher still uses `compose.yaml`.

## Native development

Python 3.12 and local PostgreSQL are required:

~~~powershell
$env:UV_CACHE_DIR = "$($PWD.Path)/.runtime/uv-cache"
uv sync --locked --group clients
# Set MOCKAZURE_DATABASE_URL to your LOCAL PostgreSQL connection URL.
uv run mockazure init
uv run mockazure db upgrade
uv run mockazure identity initialize
uv run mockazure serve
# In a second terminal:
uv run mockazure worker
~~~

After Docker initialization, native tools can use .runtime/instance/native.json.
Use a separate listen port when running a native API beside Docker.

## Evidence and compatibility

[API coverage](COVERAGE.md) maps Microsoft URIs to implemented methods, limits and gaps.
[Capabilities](docs/capabilities.md) summarizes implemented surfaces.
[Compatibility](docs/compatibility.md) distinguishes API coverage, real-client
qualification and known upstream output defects. [Documentation](docs/index.md)
links current operations and contracts; completed milestone records are indexed
under [the historical archive](docs/archive/m1-m6/README.md).

The last fully qualified release baseline is commit 8c7287c: five client suites,
44 explicitly patched mail collector cases, reproducible artifacts, and exact
100,000-user/1,000,000-record paging with restart recovery.
[Measurements and limitations](docs/stabilization-results.md) identify that source;
they are not automatically proof of later code changes.

Current work promotes the tested runtime defaults and expands persistent evidence
coverage from pinned collector requirements. Mailbox settings do not execute mail
delivery; inventory does not deploy Azure resources. No Microsoft service equivalence
or throughput SLA is claimed.

Apache-2.0. The authorized source baseline is retained under reference/ with hashes
in reference/provenance.json. It is not imported or packaged.

Security evidence imports and collection queries are documented in
[Collection evidence](docs/collection-evidence.md), including the original-source
collector inventory and explicit remaining coverage gaps.
