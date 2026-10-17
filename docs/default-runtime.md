# Default runtime and data preservation

The ordinary compose.yaml now follows the qualified release startup pattern:
PostgreSQL 18 in Docker-managed Linux storage, TCP readiness with a 180-second
startup grace, 120-second database stop grace, migrations followed by identity
initialization, then API and worker startup. HTTPS and PostgreSQL ingress bind to
loopback. Application/database services remain on an internal network.

Run mockazure init --compose before starting Compose. Its non-secret compose.env
contains MOCKAZURE_POSTGRES_STORAGE. The Compose file requires this value.

## Existing bind-backed installations

Initialization recognizes the old two-variable compose.env only when the paired
.runtime/postgres directory exists. It atomically adds the explicit legacy bind
selector and preserves all database bytes, credentials, signing identity and CA.
Repeated initialization is idempotent. Conflicting configuration or missing legacy
data fails before replacing it. This is compatibility, not a database migration.

A fresh instance records postgres-data, scoped by the Compose project name.
Never switch an existing instance's selector by hand. Never remove a volume or
database directory to bypass a check. A missing Docker volume also requires restoring
the matching operational backup; initialization cannot inspect Docker volume inventory
when it runs in a network-disabled bootstrap container.

To start a deliberately separate fresh lab, stop the owned old project and preserve
its complete runtime and database together. Use a separate project directory and
Compose project name. Do not reuse old credentials as a substitute for restoring data.

## Backup and stop

Use PostgreSQL's supported backup tools and retain the corresponding complete instance
configuration, wrapping key, certificates and credentials securely. Public tenant or
directory exports are not operational backups. Restoring a database without its
paired instance identity can make encrypted signing material unusable.

docker compose --env-file .runtime/compose.env stop preserves data.
Do not use down --volumes unless deliberately deleting that lab's database.

## Verification

python -m tools.verify_default builds the current source and initializes a fresh
isolated default-profile lab under work/. It checks the actual volume mount, HTTPS
readiness, identity readiness, tenant persistence and API/database/worker restart,
then stops its containers and preserves data. Each run uses unique project ownership
and free loopback ports. Reports contain no credentials.
