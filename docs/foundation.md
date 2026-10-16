# Foundation operations

## Configuration and identity

Every command accepts `--config PATH` where applicable; the default is
`.runtime/instance/native.json`. Configuration is JSON. `MOCKAZURE_` environment
variables override known fields. Use JSON arrays for `MOCKAZURE_ALLOWED_HOSTS`.
Relative `data_dir` values resolve against the configuration file, not the
working directory.

The required database URL uses `postgresql://` or `postgresql+psycopg://`.
Native Windows uses an asyncio selector loop for psycopg. Each API/worker owns a
bounded pool: 5 connections plus 5 overflow by default. Account for all replicas
when sizing PostgreSQL connection limits.

The instance directory contains:

| File | Purpose |
| --- | --- |
| native.json | Native paths, PostgreSQL URL, listener and advertised endpoint |
| container.json | Same instance, container network/database configuration |
| admin.token | Random 256-bit management credential |
| ca.pem / ca.key | Local CA certificate and private key |
| server.pem / server.key | HTTPS leaf certificate and private key |

Compose additionally uses `.runtime/db.password` and `.runtime/compose.env`.
The env file contains ports, not passwords. PostgreSQL reads its password from a
mounted secret file. Do not print or share configuration files containing DSNs.

Initialization is exclusive across processes. It reuses a complete identity and
refuses partial, corrupt or conflicting state. Interrupted initialization may
leave a lock file: stop all initializers, inspect the instance, and restore
complete files before removing the stale lock. Never remove a lock while an
initializer is active. There is no automatic secret/CA replacement.

Files are created with mode 0600 on POSIX. Windows uses the project directory's
inherited ACL; restrict that directory to intended users. Nothing modifies
Windows trust stores, DNS, hosts files, or global Git settings.

## HTTPS

Clients should trust `ca.pem` explicitly; for example, curl's `--cacert` or an
HTTPX SSL context. The CLI does this automatically and never follows redirects.
Do not disable certificate verification.

The leaf contains localhost, 127.0.0.1, and configured aliases. Native default
binding is 127.0.0.1; Docker binds inside its isolated network and publishes only
loopback ports. Host headers must match allowed_hosts. URLs in Location headers
come from the configured admin_base_url.

CA lifetime is 10 years; leaf lifetime is 365 days, with five minutes of clock
skew allowance. Renewal is explicit:

```text
mockazure cert renew --config .runtime/instance/native.json
```

Renew on the writable host instance directory, then restart API processes.
Renewal preserves the CA; runtime validation is read-only and fails on missing,
expired or mismatched material. Back up the CA key before renewal. An interrupted
two-file leaf replacement fails validation and requires another explicit renewal.

## PostgreSQL and Docker

Schema changes use `mockazure db upgrade`, never an implicit API startup migration.
A PostgreSQL advisory transaction lock serializes concurrent migrations on the
same connection Alembic uses. `db status` and `/_mock/ready` distinguish readiness
from simple process liveness.

PostgreSQL 18 mounts **/var/lib/postgresql**, including its versioned
`18/docker` directory. Host storage is `.runtime/postgres`.
Container recreation keeps the actual database files and instance credentials.
Do not run volume deletion or erase these directories to troubleshoot startup.

The API, worker, migration runner and database attach only to an internal Docker
network. A pinned HAProxy TCP ingress connects that network to loopback-published
HTTPS and PostgreSQL ports. It performs no TLS termination, authentication or
response synthesis. This extra service is needed because internal Docker bridges
on this Windows/Docker host do not publish usable native-client ports. Its external
bridge disables outbound masquerading; service isolation relies on the internal
network, not that setting alone.

[Docker's networking documentation](https://docs.docker.com/engine/network/)
describes the internal/backend plus ingress network pattern. Images are pinned
by digest in the Dockerfile and Compose files. Dependency installation happens
during image preparation; application startup needs no internet.

The image is built from a wheel, with lockfile-derived hashed dependencies.
The source reference and test/runtime files are excluded. Native qualification
used CPython 3.12.13; the pinned container provides CPython 3.12.15.

## Tenant administration and exports

Tenant metadata includes a server-created UUID, unique lowercase slug and domain,
display name, licence labels, revision and creation time. Licences are labels in
M1; they do not yet model Microsoft entitlements. Tenant lists use UUID keyset
pagination with a maximum of 500 entries.

Export jobs capture metadata when enqueued. Workers claim jobs with row locks
and SKIP LOCKED. A 30-second lease, owner UUID and attempt number fence completion.
Failures retry after 1 then 2 seconds, with a maximum of 3 attempts; expired final
leases are marked failed during worker polling. Results are durable and scoped to
their tenant. Poll using `job show TENANT_UUID JOB_UUID`.

`tenant export` returns a job unless `--wait` or `--output` is supplied.
Waiting has an explicit `--timeout` (default 60 seconds). Output files are
exclusive-create, never silently overwritten. `tenant import FILE` validates
schema_version=1 and always creates a new UUID. Optional slug/domain/display-name
arguments replace metadata; duplicates fail. Exports reject credentials and unknown
fields and contain no keys or sessions.

## Backup

A tenant metadata export is not an instance backup. Back up PostgreSQL using its
supported logical or physical backup tools, and back up the instance directory
and Compose database credential together. Stop writes/workers or use a coordinated
database backup procedure. Preserve permissions and restore the same CA/identity.
M1 does not provide full backup automation or tenant reset.

## Verification

Keep all caches and logs inside the project. Prepare the test database password:

```powershell
uv run python tools/prepare_tests.py
docker compose -f compose.test.yaml up -d --wait
uv run pytest tests/unit tests/integration -q
uv run ruff check .
uv build
docker compose --env-file .runtime/compose.env build
docker compose --env-file .runtime/compose.env up -d
uv run python tools/verify_foundation.py --config .runtime/instance/native.json --report work/foundation-report.json
uv run pytest tests/acceptance -q
```

The test PostgreSQL instance uses a separate Compose project, port 55433,
`.runtime/test-postgres`, and randomly named mockazure_test_ databases.
Tests create/drop only those generated databases. They never use SQLite.

The live verifier operates only on this checkout's generated Compose config.
It creates test tenants/exports and recreates this instance's API, worker and
database containers without deleting storage. Run it on the local development
instance; its test tenants remain for inspection.

Native qualification restricts Python socket/DNS activity to loopback and verifies
a nonlocal attempt is blocked before network access. Native PostgreSQL URLs point
only to loopback during qualification. Container qualification checks internal-only
network membership and failed external connection probes. These checks describe
the tested application processes, not unrelated programs on the computer.

The report records real HTTPS, two APIs, two workers, persistence across database
container replacement, CA/credential continuity, Docker-only bootstrap and a clean
offline wheel installation. It contains no secret values or token hashes.
Prepare uv's dependency cache with `uv sync --locked` before offline wheel checks.

Acceptance requires a current report; running pytest before the live verifier
intentionally fails acceptance. Runtime logs, reports and generated files remain
ignored under `work/` and `.runtime/`.
