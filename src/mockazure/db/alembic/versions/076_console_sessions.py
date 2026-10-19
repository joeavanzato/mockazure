"""Shared lab credentials, opaque sessions and replica-wide login throttling."""

from alembic import op

revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE console_credential (id integer PRIMARY KEY CHECK(id=1), encoded text NOT NULL,
 generation integer NOT NULL);
CREATE TABLE console_sessions (id uuid PRIMARY KEY, token_hash varchar(64) NOT NULL UNIQUE,
 csrf_token varchar(64) NOT NULL, generation integer NOT NULL, expires_at timestamptz NOT NULL,
 revoked boolean NOT NULL DEFAULT false);
CREATE INDEX ix_console_sessions_expires_at ON console_sessions(expires_at);
CREATE TABLE console_login_failures (id uuid PRIMARY KEY, source varchar(64) NOT NULL,
 occurred_at timestamptz NOT NULL);
CREATE INDEX ix_console_login_failures_occurred_at ON console_login_failures(occurred_at);
""")


def downgrade():
    for table in ("console_login_failures", "console_sessions", "console_credential"):
        op.execute("DROP TABLE " + table)
