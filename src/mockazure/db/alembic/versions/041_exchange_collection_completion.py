"""Persist typed Exchange configuration and mailbox permission evidence."""

from alembic import op

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE mail_exchange_configuration (
 tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
 organization jsonb NOT NULL DEFAULT '{}', transport jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE mail_exchange_configuration_records (
 tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 kind varchar(20) NOT NULL CHECK (kind IN ('acceptedDomain','transportRule')),
 id uuid NOT NULL, ordinal integer NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY (tenant_id,kind,id)
);
CREATE TABLE mail_exchange_permission_records (
 tenant_id uuid NOT NULL, family varchar(10) NOT NULL CHECK (family IN ('mailbox','recipient')),
 id uuid NOT NULL, mailbox_user_id uuid NOT NULL, ordinal integer NOT NULL, payload jsonb NOT NULL,
 PRIMARY KEY (tenant_id,family,id),
 FOREIGN KEY (tenant_id,mailbox_user_id)
 REFERENCES mail_mailboxes(tenant_id,user_id) ON DELETE CASCADE
);
CREATE INDEX mail_exchange_permission_mailbox_idx
 ON mail_exchange_permission_records(tenant_id,mailbox_user_id,family);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM mail_exchange_configuration
 WHERE organization <> '{}'::jsonb OR transport <> '{}'::jsonb)
 OR EXISTS (SELECT 1 FROM mail_exchange_configuration_records)
 OR EXISTS (SELECT 1 FROM mail_exchange_permission_records) THEN
 RAISE EXCEPTION 'Export and clear Exchange collection state before downgrade';
 END IF;
END $$;
DROP TABLE mail_exchange_permission_records, mail_exchange_configuration_records,
 mail_exchange_configuration;
""")
