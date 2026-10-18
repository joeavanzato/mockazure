"""Preserve actual password authentication time across OAuth flows."""

from alembic import op

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("oauth_authorizations", "oauth_devices", "oauth_sessions"):
        # Existing sessions have no trustworthy authentication timestamp. Keep NULL.
        op.execute(f"ALTER TABLE {table} ADD COLUMN authenticated_at timestamptz")


def downgrade():
    for table in ("oauth_authorizations", "oauth_devices", "oauth_sessions"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN authenticated_at")
