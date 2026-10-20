"""Wall-time session revocation watermark and tenant-bound manager references."""

from alembic import op

revision = "078"
down_revision = "077"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE identity_users ADD COLUMN sessions_valid_from timestamptz;
ALTER TABLE identity_users ADD COLUMN manager_id uuid;
ALTER TABLE identity_users ADD CONSTRAINT fk_identity_user_manager
 FOREIGN KEY (tenant_id, manager_id) REFERENCES identity_users(tenant_id, id);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM identity_users
            WHERE sessions_valid_from IS NOT NULL OR manager_id IS NOT NULL)
 THEN RAISE EXCEPTION 'Cannot discard populated identity lifecycle state';
 END IF;
END $$;
ALTER TABLE identity_users DROP CONSTRAINT fk_identity_user_manager;
ALTER TABLE identity_users DROP COLUMN manager_id;
ALTER TABLE identity_users DROP COLUMN sessions_valid_from;
""")
