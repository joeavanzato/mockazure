"""Persist mailbox content, versions and durable mailbox-scoped continuations."""

from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE mail_content_state (
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,
 revision bigint NOT NULL DEFAULT 0 CHECK(revision>=0),
 generation uuid NOT NULL,settings jsonb,PRIMARY KEY(tenant_id,user_id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES mail_mailboxes(tenant_id,user_id) ON DELETE CASCADE
);
CREATE TABLE mail_content_folders (
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,id varchar(128) NOT NULL,parent_id varchar(128),
 is_hidden boolean NOT NULL DEFAULT false,well_known_name varchar(64),
 PRIMARY KEY(tenant_id,user_id,id),UNIQUE(tenant_id,user_id,well_known_name),
 FOREIGN KEY(tenant_id,user_id,id) REFERENCES mail_folders(tenant_id,user_id,id) ON DELETE CASCADE
);
CREATE TABLE mail_messages (
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,immutable_id uuid NOT NULL,id varchar(512) NOT NULL,
 folder_id varchar(128) NOT NULL,revision bigint NOT NULL,payload jsonb NOT NULL,
 mime_content_id uuid,size_bytes bigint NOT NULL DEFAULT 0 CHECK(size_bytes>=0),
 attachments jsonb NOT NULL DEFAULT '[]',PRIMARY KEY(tenant_id,user_id,immutable_id),
 UNIQUE(tenant_id,user_id,id),FOREIGN KEY(tenant_id,user_id,folder_id)
 REFERENCES mail_folders(tenant_id,user_id,id)
);
CREATE INDEX mail_message_folder ON mail_messages(tenant_id,user_id,folder_id,immutable_id);
CREATE TABLE mail_message_versions (
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,immutable_id uuid NOT NULL,
 valid_from bigint NOT NULL,valid_to bigint,
 id varchar(512) NOT NULL,folder_id varchar(128) NOT NULL,
 deleted boolean NOT NULL DEFAULT false,payload jsonb NOT NULL,mime_content_id uuid,
 size_bytes bigint NOT NULL,attachments jsonb NOT NULL DEFAULT '[]',
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,user_id,immutable_id,valid_from),
 FOREIGN KEY(tenant_id,user_id) REFERENCES mail_mailboxes(tenant_id,user_id) ON DELETE CASCADE
);
CREATE INDEX mail_version_snapshot
 ON mail_message_versions(tenant_id,user_id,folder_id,valid_from,valid_to);
CREATE TABLE mail_content_cursors (
 token_hash varchar(64) PRIMARY KEY,tenant_id uuid NOT NULL,user_id uuid NOT NULL,
 state jsonb NOT NULL,
 expires_at timestamptz NOT NULL,FOREIGN KEY(tenant_id,user_id)
 REFERENCES mail_mailboxes(tenant_id,user_id) ON DELETE CASCADE
);
CREATE INDEX mail_cursor_cleanup ON mail_content_cursors(tenant_id,user_id,expires_at);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM mail_content_state) THEN
 RAISE EXCEPTION 'Export and clear mailbox content before downgrade';
 END IF;
END $$;
DROP TABLE mail_content_cursors,mail_message_versions,mail_messages,
 mail_content_folders,mail_content_state;
""")
