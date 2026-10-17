"""Enforce tenant-local uniqueness for the device alternate key."""

from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE identity_credentials ALTER COLUMN starts_at SET DEFAULT now();
CREATE UNIQUE INDEX security_device_alternate_key
ON security_objects (tenant_id, (properties->>'deviceId'))
WHERE kind = 'device';
""")


def downgrade():
    op.execute("DROP INDEX security_device_alternate_key")
    op.execute(
        "ALTER TABLE identity_credentials ALTER COLUMN starts_at SET DEFAULT '1970-01-01T00:00:00Z'"
    )
