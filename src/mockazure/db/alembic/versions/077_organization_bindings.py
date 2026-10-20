"""Independent, generation-bound identities for connected organization profiles."""

from alembic import op

revision = "077"
down_revision = "076"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE organization_namespaces (
 tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
 namespace varchar(128) NOT NULL, generation uuid NOT NULL,
 PRIMARY KEY (tenant_id, namespace));
CREATE TABLE organization_bindings (
 tenant_id uuid NOT NULL, namespace varchar(128) NOT NULL,
 logical_key varchar(128) NOT NULL, service varchar(64) NOT NULL,
 actual_id varchar(2048) NOT NULL, parent_id varchar(2048), generation uuid NOT NULL,
 PRIMARY KEY (tenant_id, namespace, logical_key, service),
 FOREIGN KEY (tenant_id, namespace) REFERENCES organization_namespaces(tenant_id, namespace)
 ON DELETE CASCADE);
CREATE INDEX ix_organization_binding_parent
 ON organization_bindings(tenant_id, namespace, service, md5(actual_id));
""")


def downgrade():
    op.drop_table("organization_bindings")
    op.drop_table("organization_namespaces")
