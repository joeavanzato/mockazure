"""Scoped, one-shot ARM Activity retry faults."""

from alembic import op

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE arm_read_faults (
 tenant_id uuid NOT NULL,
 subscription_id uuid NOT NULL,
 client_id uuid NOT NULL,
 kind text NOT NULL CHECK (kind IN ('next_read_429','next_page_429')),
 retry_after integer NOT NULL CHECK (retry_after BETWEEN 1 AND 300),
 PRIMARY KEY (tenant_id,subscription_id,client_id),
 FOREIGN KEY (tenant_id,subscription_id)
 REFERENCES arm_subscriptions(tenant_id,id) ON DELETE CASCADE,
 FOREIGN KEY (tenant_id,client_id)
 REFERENCES identity_service_principals(tenant_id,client_id) ON DELETE CASCADE
);
""")


def downgrade():
    op.execute("DROP TABLE arm_read_faults")
