"""Keep legacy roles activated even after the last assignment is removed."""

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE security_role_activations (
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    role varchar(80) NOT NULL,
    PRIMARY KEY (tenant_id, role)
);
INSERT INTO security_role_activations (tenant_id, role)
SELECT tenant_id, role FROM identity_directory_roles
UNION SELECT tenant_id, role FROM identity_service_principal_roles;
""")


def downgrade():
    op.execute("DROP TABLE security_role_activations")
