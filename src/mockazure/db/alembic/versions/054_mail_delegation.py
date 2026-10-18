"""Authoritative Exchange mailbox delegation."""

from alembic import op

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE mail_delegations (
        tenant_id uuid NOT NULL, mailbox_id uuid NOT NULL, principal_id uuid NOT NULL,
        permission varchar(20) NOT NULL CHECK(permission IN ('FullAccess','SendAs','SendOnBehalf')),
        deny boolean NOT NULL DEFAULT false,
        inheritance varchar(20) NOT NULL DEFAULT 'None'
            CHECK(inheritance IN ('None','All','Children','Descendents','SelfAndChildren')),
        auto_mapping boolean NOT NULL DEFAULT true,
        PRIMARY KEY(tenant_id,mailbox_id,principal_id,permission,deny,inheritance),
        FOREIGN KEY(tenant_id,mailbox_id)
            REFERENCES mail_mailboxes(tenant_id,user_id) ON DELETE CASCADE,
        FOREIGN KEY(tenant_id,principal_id)
            REFERENCES directory_entries(tenant_id,id) ON DELETE CASCADE,
        CHECK(NOT deny OR permission = 'FullAccess')
    );
    CREATE INDEX ix_mail_delegation_principal ON mail_delegations(tenant_id,principal_id);
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM mail_delegations)
        THEN RAISE EXCEPTION 'Cannot discard operational mailbox delegation'; END IF;
    END $$;""")
    op.execute("DROP TABLE mail_delegations")
