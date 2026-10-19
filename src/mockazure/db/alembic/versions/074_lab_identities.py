"""Encrypted named identities for local population service actions."""

from alembic import op

revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE lab_identities (
tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
name varchar(256) NOT NULL,client_id uuid NOT NULL,encrypted_secret text NOT NULL,
definition jsonb NOT NULL,revision integer NOT NULL DEFAULT 1,
PRIMARY KEY(tenant_id,name),
FOREIGN KEY(tenant_id,client_id)
REFERENCES identity_applications(tenant_id,client_id) ON DELETE CASCADE);""")


def downgrade():
    op.execute("DROP TABLE lab_identities")
