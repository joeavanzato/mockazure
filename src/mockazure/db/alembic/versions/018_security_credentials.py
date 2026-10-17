"""Credential lifetime metadata and reversible directory object deletion."""

from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE identity_applications ADD COLUMN deleted_at timestamptz;
ALTER TABLE identity_service_principals ADD COLUMN deleted_at timestamptz;
ALTER TABLE identity_credentials ADD COLUMN starts_at timestamptz NOT NULL
 DEFAULT '1970-01-01T00:00:00Z';
ALTER TABLE identity_credentials ADD COLUMN display_name varchar(200);
ALTER TABLE identity_credentials ADD COLUMN owner_id uuid;
""")


def downgrade():
    op.execute("""
ALTER TABLE identity_credentials DROP COLUMN owner_id;
ALTER TABLE identity_credentials DROP COLUMN display_name;
ALTER TABLE identity_credentials DROP COLUMN starts_at;
ALTER TABLE identity_service_principals DROP COLUMN deleted_at;
ALTER TABLE identity_applications DROP COLUMN deleted_at;
""")
