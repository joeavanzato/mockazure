"""Creation time for new grants; preserve unknown historical timestamps as null."""

from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE identity_grants ADD COLUMN created_at timestamptz")
    op.execute("ALTER TABLE identity_grants ALTER COLUMN created_at SET DEFAULT now()")
    op.execute("""
CREATE TABLE security_grant_role_metadata (
    grant_id uuid NOT NULL REFERENCES identity_grants(id) ON DELETE CASCADE,
    permission varchar(200) NOT NULL,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (grant_id, permission)
);
""")


def downgrade():
    op.execute("DROP TABLE security_grant_role_metadata")
    op.execute("ALTER TABLE identity_grants DROP COLUMN created_at")
