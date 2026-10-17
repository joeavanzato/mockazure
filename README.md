# MockAzure

MockAzure is a general-purpose Microsoft cloud API emulator focused on evidence
collection and realistic stateful interactions.

**Current development milestone: M5, Azure management and Activity Logs.** Local
Entra authentication, Graph directory/security operations, mailbox configuration,
inbox/sweep rules, message traces and durable audit queries use persisted
PostgreSQL state and permission checks. Original Microsoft clients and selected
Hawk/Microsoft Extractor Suite workflows make real local HTTP requests.

M4's required collector gate remains **unresolved**: original Hawk investigation
JSON is malformed for multiple suspicious rules, and original MES trace CSV
drops fractional seconds. These failures are retained without new waivers.
The repaired API and its tests are documented in
[mail execution](docs/mail-execution.md) and [final review](docs/mail-review.md). See the
[mail contract](docs/mail-contracts.md), [capability matrix](docs/capabilities.md)
and [compatibility boundaries](docs/compatibility.md) for the exact supported
operations and limitations. Mailbox settings do not execute mail delivery.
ARM includes subscription/resource inventory, resource-group and tag mutations,
current inherited RBAC, four built-in roles, durable group deletion and immutable
Activity Logs with exact100ns filtering and paging. Original Az PowerShell,
Python Azure SDKs and MES use real local HTTPS. See [ARM contracts](docs/arm-contracts.md)
and [execution](docs/arm-execution.md). Combined release/scale qualification follows
in M6; the four M4 collector failures remain blockers.

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
MSYS_NO_PATHCONV=1 docker run --rm --network none --mount "type=bind,source=$(pwd
-W),target=/project" mockazure:local init --compose --project-dir /project
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

## Stateful Graph evidence (M3B)

PostgreSQL-backed sign-ins, directory audits and Identity Protection now include
immutable native evidence, versioned reads, risk actions, licence enforcement,
App Service managed identity, and administrative import/export, clock, delay,
retention and retry controls. Resource schemas follow pinned Microsoft Graph
v1.0 metadata and REST references; beta-only fields are separated.

The final combined qualification passed all 25 runtime gates, three native/container SDK
profiles in both authorization modes, and all 20 collector acceptance gates (14 strict passes; 6
explicitly accepted original MES audit JSON limitations). SDK/runtime checks took 581.622
seconds; the complete run took 2842.423 seconds. The report is bound to source, wheel, lockfile,
manifest, original collector files, outputs and the running image.

The original MES audit JSON append/merge defect is a documented exception; its
strict failures stay visible. See [execution and operation boundaries](docs/evidence-execution.md),
[measured verification](docs/evidence-verification.json) and
[compatibility](docs/compatibility.md). These are measured functional profiles,
not full Microsoft-cloud compatibility or a throughput SLA.

## Stateful Graph security administration (M3C)

Applications, service principals, devices, owners, credentials, API permission
grants and tenant-wide directory roles now use persisted state with operational
authorization. Authentication-method inspection and registration reports reflect
enrollment state. Conditional Access evaluates the declared user/group/resource/
client-category and IP-location profile with block, simulated MFA and report-only
outcomes. Policy decisions become immutable sign-in evidence with controlled disclosure.

Final qualification passed20 runtime gates, three SDK profiles in both authorization
modes and20 original Hawk/MES security collector cases in 181.5455 seconds.
The full regression passed 522 tests in 580.74s. One independent final
review identified five important findings; reproduced fixes cover policy UUIDs,
managed credentials/tombstones, owner lifecycle and denied collector JSON.
See [security execution](docs/security-execution.md) for verification and the
retained upstream licence-notice whitespace minor. These are bounded functional
results, not full Graph coverage or a throughput SLA.

See [operation boundaries](docs/security-contracts.md),
[client/collector contract](integrations/contracts/graph-security.md),
[verification evidence](docs/security-verification.json) and
[decisions](docs/security-decisions.md).

The isolated M3C runtime owns .runtime/m3c, HTTPS 32443, PostgreSQL 55440 and
temporary native API ports 32444/32445. It does not change system trust/DNS/firewall.
Prepare pinned public dependencies before running isolated clients:

~~~powershell
uv sync --frozen --all-groups
python tools/prepare_clients.py --manifest integrations/manifest.json --output work/vendor
python tools/prepare_security.py initialize
docker network create --internal --subnet 10.231.90.0/24 mockazure-m3c-clients-isolated
$sourceFingerprint = python -c "from tools.verify_security import source_fingerprint; print(source_fingerprint())"
docker build --build-arg "MOCKAZURE_SOURCE_FINGERPRINT=$sourceFingerprint" -t mockazure:security .
docker build -f integrations/Dockerfile.python -t mockazure:security-python-clients .
docker compose -f compose.m3c.yaml up -d --wait
python tools/prepare_security.py provision
uv build --wheel
python tools/verify_security.py --report work/security-report.json
~~~

Create the network only once; verify its internal flag on subsequent runs.
Runtime client profiles and generated credentials remain ignored project-local files.
The verifier requires unchanged source bytes, pinned artifacts, clean-wheel parity,
real client requests, exact collector files, denied access and restart persistence.

## Exchange, Azure management and release qualification

The Exchange/UAL milestone adds persisted mailbox, rule, trace and audit behavior,
including the bounded Microsoft Exchange module bootstrap. Four original collector
output checks remain blocked: Hawk inbox-rule JSON concatenation in both auth modes,
and MES trace CSV timestamp precision in both modes. See
[mail verification](docs/mail-verification.json) and [mail contracts](docs/mail-contracts.md).

The Azure management milestone adds the declared subscription/resource-group/tag,
RBAC, asynchronous group deletion and Activity Log profile. Its measured runtime,
SDK and original MES cases pass; its stacked PR remains draft because the combined
M4 blockers remain. See [ARM verification](docs/arm-verification.json) and
[ARM contracts](docs/arm-contracts.md).

M6 adds bounded storage maintenance, deterministic scale/recovery measurements,
and reproducible candidate artifact tooling. Historical sign-in projection uses
bounded atomic batches, individual receipts and generation/epoch fences; missing-job
repair runs periodically. These changes do not promise production-cloud throughput.
See [maintenance](docs/maintenance.md), [scale measurements](docs/release-scale.md),
[build instructions](docs/release-build.md) and [execution status](docs/release-execution.md).

A candidate artifact pass is separate from release acceptance. Every original-client
suite and all three declared scale profiles must pass on the current source before
release_ready can be true. The existing M3B MES audit JSON exception is the only
accepted collector-output waiver. No package or image is published automatically.
