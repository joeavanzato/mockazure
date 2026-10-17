"""Immutable trace recipients/details and cross-replica rolling budgets."""

from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE mail_traces (
 tenant_id uuid NOT NULL REFERENCES tenants(id), message_trace_id uuid NOT NULL,
 recipient_address varchar(320) NOT NULL, received timestamptz NOT NULL,
 sender_address varchar(320) NOT NULL, mailbox_user_id uuid,
 message_id varchar(1024) NOT NULL, subject varchar(2048) NOT NULL,
 status varchar(32) NOT NULL, size bigint NOT NULL CHECK(size>=0),
 from_ip varchar(45), to_ip varchar(45), provenance varchar(512) NOT NULL,
 payload jsonb NOT NULL,
 PRIMARY KEY(tenant_id,message_trace_id,recipient_address),
 UNIQUE(tenant_id,received,recipient_address),
 FOREIGN KEY(tenant_id,mailbox_user_id) REFERENCES mail_mailboxes(tenant_id,user_id)
);
CREATE INDEX mail_trace_window_idx ON mail_traces(tenant_id,received DESC,recipient_address);
CREATE INDEX mail_trace_sender_idx ON mail_traces(tenant_id,sender_address,received DESC);
CREATE INDEX mail_trace_scope_idx ON mail_traces(tenant_id,mailbox_user_id,received DESC);
CREATE TABLE mail_trace_details (
 tenant_id uuid NOT NULL, message_trace_id uuid NOT NULL,
 recipient_address varchar(320) NOT NULL, sequence integer NOT NULL,
 occurred_at timestamptz NOT NULL, event varchar(128) NOT NULL,
 action varchar(256) NOT NULL, detail text NOT NULL, data text NOT NULL,
 PRIMARY KEY(tenant_id,message_trace_id,recipient_address,sequence),
 FOREIGN KEY(tenant_id,message_trace_id,recipient_address)
 REFERENCES mail_traces(tenant_id,message_trace_id,recipient_address)
);
CREATE TABLE mail_trace_budgets (
 tenant_id uuid NOT NULL REFERENCES tenants(id), command varchar(64) NOT NULL,
 requests jsonb NOT NULL DEFAULT '[]', PRIMARY KEY(tenant_id,command)
);
""")


def downgrade():
    op.execute("DROP TABLE mail_trace_budgets, mail_trace_details, mail_traces")
