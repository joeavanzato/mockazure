"""Operational Exchange RBAC membership and assignments."""

from alembic import op

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE mail_role_members (
        tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        group_name varchar(64) NOT NULL, principal_id uuid NOT NULL,
        PRIMARY KEY(tenant_id,group_name,principal_id),
        FOREIGN KEY(tenant_id,principal_id)
            REFERENCES directory_entries(tenant_id,id) ON DELETE CASCADE
    );
    CREATE TABLE mail_role_assignments (
        tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        id uuid NOT NULL, name varchar(128) NOT NULL, name_key varchar(128) NOT NULL,
        role varchar(64) NOT NULL, principal_id uuid, group_name varchar(64),
        recipient_group_id uuid,
        PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,name_key),
        FOREIGN KEY(tenant_id,principal_id)
            REFERENCES directory_entries(tenant_id,id) ON DELETE CASCADE,
        FOREIGN KEY(tenant_id,recipient_group_id)
            REFERENCES directory_groups(tenant_id,id) ON DELETE CASCADE,
        CHECK((principal_id IS NULL) <> (group_name IS NULL))
    );
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM mail_role_members) OR EXISTS(SELECT 1 FROM mail_role_assignments)
      THEN RAISE EXCEPTION 'Cannot discard operational Exchange role state'; END IF;
    END $$;""")
    op.execute("DROP TABLE mail_role_assignments; DROP TABLE mail_role_members;")
