"""Durable local mail deliveries and expiring upload capabilities."""

from alembic import op

revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE mail_deliveries (
 id uuid PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,message_id uuid NOT NULL,
 generation uuid NOT NULL,evidence_generation uuid NOT NULL,cloud_generation bigint NOT NULL,
 actor jsonb NOT NULL,represented_id uuid NOT NULL,
 mode varchar(20) NOT NULL,save_sent boolean NOT NULL,outcome jsonb,
 FOREIGN KEY(tenant_id,user_id) REFERENCES mail_content_state(tenant_id,user_id) ON DELETE CASCADE);
CREATE INDEX mail_delivery_message ON mail_deliveries(tenant_id,user_id,message_id);
CREATE TABLE mail_attachment_uploads (
 token_hash varchar(64) PRIMARY KEY,tenant_id uuid NOT NULL,user_id uuid NOT NULL,
 message_id uuid NOT NULL,generation uuid NOT NULL,evidence_generation uuid NOT NULL,
 cloud_generation bigint NOT NULL,content_id uuid NOT NULL,
 expected bigint NOT NULL CHECK(expected BETWEEN 3145728 AND 157286400),
 received bigint NOT NULL CHECK(received >= 0 AND received <= expected),
 item jsonb NOT NULL,actor jsonb NOT NULL,expires_at timestamptz NOT NULL,
 FOREIGN KEY(tenant_id,user_id) REFERENCES mail_content_state(tenant_id,user_id)
 ON DELETE CASCADE,
 FOREIGN KEY(tenant_id,user_id,message_id)
 REFERENCES mail_messages(tenant_id,user_id,immutable_id) ON DELETE CASCADE);
CREATE INDEX mail_attachment_upload_expiry
 ON mail_attachment_uploads(tenant_id,expires_at,token_hash);
""")


def downgrade():
    op.execute("""DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM mail_deliveries) OR EXISTS(SELECT 1 FROM mail_attachment_uploads)
 THEN RAISE EXCEPTION 'Reset mail workflow state before downgrade'; END IF; END $$;
DROP TABLE mail_attachment_uploads;
DROP TABLE mail_deliveries;""")
