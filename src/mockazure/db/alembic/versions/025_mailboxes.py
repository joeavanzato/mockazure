"""Canonical mailbox state and current Exchange command scopes."""

from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE mail_settings (
 tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
 exchange_enabled boolean NOT NULL DEFAULT false,
 audit_tier varchar(10) NOT NULL DEFAULT 'none' CHECK (audit_tier IN ('none','standard','premium'))
);
CREATE TABLE mail_mailboxes (
 tenant_id uuid NOT NULL, user_id uuid NOT NULL, exchange_guid uuid NOT NULL,
 primary_smtp varchar(320) NOT NULL, alias varchar(128) NOT NULL,
 aliases jsonb NOT NULL DEFAULT '[]',
 mailbox_type varchar(30) NOT NULL DEFAULT 'UserMailbox'
  CHECK (mailbox_type IN ('UserMailbox','SharedMailbox','RoomMailbox','EquipmentMailbox')),
 archive_guid uuid, cas jsonb NOT NULL DEFAULT '{}',
 forward_user_id uuid, forward_smtp varchar(325),
 deliver_and_forward boolean NOT NULL DEFAULT false,
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (tenant_id,user_id),
 FOREIGN KEY (tenant_id,user_id) REFERENCES identity_users(tenant_id,id),
 FOREIGN KEY (tenant_id,forward_user_id) REFERENCES identity_users(tenant_id,id),
 UNIQUE (tenant_id,exchange_guid), UNIQUE (tenant_id,primary_smtp), UNIQUE (tenant_id,archive_guid)
);
CREATE INDEX mail_alias_idx ON mail_mailboxes(tenant_id,alias);
CREATE TABLE mail_folders (
 tenant_id uuid NOT NULL, user_id uuid NOT NULL, id varchar(128) NOT NULL,
 name varchar(256) NOT NULL, path varchar(1024) NOT NULL, archive boolean NOT NULL DEFAULT false,
 item_count bigint NOT NULL DEFAULT 0 CHECK (item_count >= 0),
 size_bytes bigint NOT NULL DEFAULT 0 CHECK (size_bytes >= 0),
 PRIMARY KEY(tenant_id,user_id,id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES mail_mailboxes(tenant_id,user_id)
);
CREATE TABLE mail_command_grants (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
 user_id uuid, client_id uuid, mailbox_user_id uuid, commands jsonb NOT NULL,
 CHECK ((user_id IS NULL) <> (client_id IS NULL)),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity_users(tenant_id,id),
 FOREIGN KEY(tenant_id,client_id)
  REFERENCES identity_service_principals(tenant_id,client_id),
 FOREIGN KEY(tenant_id,mailbox_user_id) REFERENCES mail_mailboxes(tenant_id,user_id)
);
CREATE INDEX mail_grant_tenant_idx ON mail_command_grants(tenant_id);
""")


def downgrade():
    op.execute("DROP TABLE mail_command_grants, mail_folders, mail_mailboxes, mail_settings")
