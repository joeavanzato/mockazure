# MockAzure

MockAzure is a general-purpose Microsoft cloud API emulator focused on evidence
collection and realistic stateful interactions.

**Current milestone: foundation.** It provides HTTPS administration, persistent
tenant metadata in PostgreSQL, and durable tenant export/import jobs.
**Entra authentication, Graph, Exchange, and ARM APIs are not implemented yet.**
Provider requests return an explicit HTTP 501. Collector compatibility and
large-dataset throughput have not been qualified.

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
