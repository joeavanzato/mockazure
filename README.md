# MockAzure

MockAzure is a general-purpose Microsoft cloud API emulator focused on evidence
collection and realistic stateful interactions.

**Current development milestone: M3B, stateful evidence APIs.** Local Entra
authentication and permission checks support directory lifecycle and Graph sign-in,
directory-audit and Identity Protection evidence, licensing operations and App
Service managed identity. PostgreSQL retains state, immutable observations and
snapshot continuations across API restarts.

The SDK qualification has passed and the independent review fixes pass regression
tests. Original collector checks expose an upstream MES audit JSON serialization
defect; the complete milestone acceptance gate remains open. See the
[evidence contract](integrations/contracts/graph-evidence.md),
[evidence operations](docs/evidence.md) and
[execution record](docs/evidence-execution.md).
Broader security administration remains M3C. Exchange and ARM retain the bounded
M2 connection surfaces.

## Docker quick start (PowerShell)

Only Docker Desktop with Linux containers is required. From the repository:

```powershell
docker build -t mockazure:local .
docker run --rm --network none --mount "type=bind,source=$($PWD.Path),target=/project" mockazure:local init --compose --project-dir /project
docker compose --env-file .runtime/compose.env up -d
docker compose --env-file .runtime/compose.env exec api mockazure capabilities --config /instance/container.json
docker compose --env-file .runtime/compose.env exec api mockazure tenant create --slug contoso --display-name "Contoso Lab" --domain contoso.test --config /instance/container.json
```

The in-container administrative client needs the published host endpoint by
default. For container-local CLI calls, set the local endpoint override:

```powershell
docker compose --env-file .runtime/compose.env exec -e MOCKAZURE_ADMIN_BASE_URL=https://localhost:8443 api mockazure tenant list --config /instance/container.json
```

With the default port the first examples already use that endpoint. If changing
the published port, use the override for all container-local client commands.

Git Bash initialization:

```bash
docker build -t mockazure:local .
MSYS_NO_PATHCONV=1 docker run --rm --network none --mount "type=bind,source=$(pwd -W),target=/project" mockazure:local init --compose --project-dir /project
docker compose --env-file .runtime/compose.env up -d
```

HTTPS is published only at `https://localhost:8443`; PostgreSQL at
`127.0.0.1:55432`. Bootstrap accepts `--port` and `--db-port`.
Data and generated secrets live under `.runtime/`, never in the source tree's
tracked files. The TCP ingress passes TLS through; the application, workers,
and database use an internal Docker network.

## Native Python / CLI

Python 3.12 and PostgreSQL are required. A wheel can be installed with pip.
For development, use uv and keep its cache/runtime in the project:

```powershell
$env:UV_CACHE_DIR = "$($PWD.Path)/.runtime/uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$($PWD.Path)/.runtime/python"
uv sync --locked
# Set MOCKAZURE_DATABASE_URL to your LOCAL PostgreSQL connection URL.
uv run mockazure init
uv run mockazure db upgrade
uv run mockazure serve
# In a second terminal, with the same project/config:
uv run mockazure worker
```

Alternatively, after Docker initialization, use the generated
`.runtime/instance/native.json`. To run a second native API beside Docker,
set `MOCKAZURE_LISTEN_PORT=9443` and
`MOCKAZURE_ADMIN_BASE_URL=https://localhost:9443`.

```powershell
uv run mockazure tenant create --slug contoso --display-name "Contoso Lab" --domain contoso.test
uv run mockazure tenant list
uv run mockazure tenant export TENANT_UUID --wait --output ./work/tenant.json
uv run mockazure tenant import ./work/tenant.json --slug contoso-copy --domain contoso-copy.test
```

See [operations and verification](docs/foundation.md), the
[capability matrix](docs/capabilities.md), and the
[design](docs/superpowers/specs/2026-10-16-mockazure-design.md).

Apache-2.0. The authorized four-file source baseline is preserved only under
`reference/`, with exact Git revision and hashes in `reference/provenance.json`.
It is not imported or shipped in the package.

Identity/connectivity (M2) now includes persistent local OAuth, Graph organization
and user probes, an Exchange connection bootstrap, and ARM subscription discovery.
See [identity operations](docs/identity.md), [exact compatibility](docs/compatibility.md)
and [capabilities](docs/capabilities.md). These are connection gates, not complete
Microsoft cloud or collector-workflow coverage.

## Isolated M3 directory qualification

Preparation downloads only pinned public dependencies; runtime uses local endpoints.
The M3 stack uses its own database, CA and ports 48443/55435. Native verification
temporarily uses 48444/48445 and never changes host trust, DNS or hosts files.

~~~powershell
python tools/prepare_clients.py --manifest integrations/manifest.json --output work/vendor
python tools/prepare_graph.py
docker network create --internal --subnet 10.231.74.0/24 mockazure-m3-clients-isolated
$sourceFingerprint = python -c "from tools.verify_graph import source_fingerprint; print(source_fingerprint())"
docker build --build-arg "MOCKAZURE_SOURCE_FINGERPRINT=$sourceFingerprint" -t mockazure:graph .
docker build -f integrations/Dockerfile.python -t mockazure:graph-python-clients .
docker compose -f compose.m3.yaml up -d
python tools/prepare_graph.py --provision
python -m build
python tools/verify_graph.py --report work/graph-directory-report.json
~~~

Run from an environment installed with the locked development and client groups.
Create the isolated network once; on later runs verify it remains internal.
Qualification uses actual MSAL/Graph Python and Microsoft Graph PowerShell
Users/Groups 2.35.1. It exercises application and delegated modes, 5,000-user
paging, two API replicas, API/PostgreSQL restart, delta reuse and rejected access.
This is a functional dataset qualification, not a throughput or scale SLA.
Generated credentials/reports stay ignored under .runtime/m3 and work.
