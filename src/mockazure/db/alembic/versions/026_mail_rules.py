"""Versioned mailbox rule configuration and stable page snapshots."""

from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE mail_mailboxes ADD COLUMN rule_revision bigint NOT NULL DEFAULT 0;
CREATE TABLE mail_rules (
 tenant_id uuid NOT NULL, user_id uuid NOT NULL, id varchar(128) NOT NULL,
 family varchar(10) NOT NULL CHECK(family IN ('inbox','sweep')),
 sequence integer NOT NULL CHECK(sequence >= 1), payload jsonb NOT NULL,
 hidden boolean NOT NULL DEFAULT false, read_only boolean NOT NULL DEFAULT false,
 PRIMARY KEY(tenant_id,user_id,id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES mail_mailboxes(tenant_id,user_id),
 UNIQUE(tenant_id,user_id,family,sequence) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE mail_rule_versions (
 tenant_id uuid NOT NULL, user_id uuid NOT NULL, id varchar(128) NOT NULL,
 valid_from bigint NOT NULL, valid_to bigint, sequence integer NOT NULL,
 family varchar(10) NOT NULL, hidden boolean NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY(tenant_id,user_id,id,valid_from),
 FOREIGN KEY(tenant_id,user_id) REFERENCES mail_mailboxes(tenant_id,user_id)
);
CREATE INDEX mail_rule_snapshot_idx ON mail_rule_versions(tenant_id,user_id,family,sequence,id);
""")


def downgrade():
    op.execute(
        "DROP TABLE mail_rule_versions, mail_rules; "
        "ALTER TABLE mail_mailboxes DROP COLUMN rule_revision"
    )
