"""Persistent occurrence overrides and cancellation tombstones."""

from alembic import op

revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE calendar_exceptions (
tenant_id uuid NOT NULL,user_id uuid NOT NULL,master_id varchar(128) NOT NULL,
day varchar(10) NOT NULL,overrides jsonb NOT NULL,deleted boolean NOT NULL,
updated_at timestamptz NOT NULL,PRIMARY KEY(tenant_id,user_id,master_id,day),
FOREIGN KEY(tenant_id,user_id,master_id)
REFERENCES calendar_events(tenant_id,user_id,id) ON DELETE CASCADE);""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM calendar_exceptions)
THEN RAISE EXCEPTION 'Reset calendar exceptions before downgrade'; END IF; END $$;
DROP TABLE calendar_exceptions;""")
