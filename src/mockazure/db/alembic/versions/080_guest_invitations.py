"""Local guest invitation records and bounded invitation policy."""

from alembic import op

revision = "080"
down_revision = "079"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE identity_guest_invitations (
 tenant_id uuid NOT NULL REFERENCES tenants(id), id uuid NOT NULL,
 user_id uuid NOT NULL, generation uuid NOT NULL, email varchar(320) NOT NULL,
 status varchar(32) NOT NULL, redirect_url varchar(2048) NOT NULL,
 message_info jsonb NOT NULL, send_message boolean NOT NULL, delivery jsonb NOT NULL,
 created_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity_users(tenant_id,id)
);
CREATE INDEX ix_guest_invitation_email ON identity_guest_invitations(tenant_id,email);
CREATE TABLE identity_guest_policies (
 tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
 enabled boolean NOT NULL, allow_member_invites boolean NOT NULL
);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM identity_guest_invitations)
 OR EXISTS(SELECT 1 FROM identity_guest_policies)
 THEN RAISE EXCEPTION 'Cannot discard guest invitation state'; END IF;
END $$;
DROP TABLE identity_guest_policies;
DROP TABLE identity_guest_invitations;
""")
