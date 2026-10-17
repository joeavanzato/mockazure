
#!/bin/sh
# Keep the initialization marker outside PostgreSQL storage so a lost volume fails closed.
set -eu
instance="${MOCKAZURE_INSTANCE_DIR:-/instance}"
cluster="${PGDATA:-/var/lib/postgresql/18/docker}"
marker="$instance/postgres.initialized"
refuse() {
    printf '%s\n' "MockAzure: refusing PostgreSQL startup: $1. Restore the paired database and instance backup; see docs/default-runtime.md." >&2
    exit 1
}
[ -d "$instance" ] || refuse "instance directory is missing"
if [ -e "$cluster/PG_VERSION" ]; then
    [ "$(cat "$cluster/PG_VERSION")" = 18 ] && [ -s "$cluster/global/pg_control" ] ||
        refuse "cluster is incomplete or has the wrong PostgreSQL version"
else
    if [ -e "$marker" ] || [ -L "$marker" ] || [ -e "$instance/identity.wrap" ] || [ -L "$instance/identity.wrap" ]; then
        refuse "previously initialized database storage is missing"
    fi
fi
if [ ! -e "$marker" ] && [ ! -L "$marker" ]; then
    (umask 077; set -C; printf '%s\n' mockazure-postgres-18 > "$marker") ||
        refuse "initialization marker could not be created exclusively"
fi
exec docker-entrypoint.sh "$@"
