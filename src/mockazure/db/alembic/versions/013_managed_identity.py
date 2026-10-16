"""Host-local managed identity bindings share application and principal identity."""

from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE identity_applications DROP CONSTRAINT identity_applications_client_mode_check;
ALTER TABLE identity_applications ADD CONSTRAINT identity_applications_client_mode_check
 CHECK(client_mode IN ('public','confidential','managed'));
CREATE TABLE managed_identities (
 id UUID PRIMARY KEY, tenant_id UUID NOT NULL, client_id UUID NOT NULL UNIQUE,
 kind VARCHAR(10) NOT NULL CHECK(kind IN ('system','user')),
 resource_id VARCHAR(2048) NOT NULL, resource_key VARCHAR(2048) NOT NULL,
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,resource_key),
 FOREIGN KEY(tenant_id,id) REFERENCES identity_service_principals(tenant_id,id),
 FOREIGN KEY(tenant_id,client_id) REFERENCES identity_applications(tenant_id,client_id)
);
CREATE INDEX ix_managed_identities_tenant ON managed_identities(tenant_id);
CREATE TABLE managed_identity_hosts (
 id UUID PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id),
 name VARCHAR(200) NOT NULL, enabled BOOLEAN NOT NULL, header_hash VARCHAR(64) NOT NULL UNIQUE,
 system_identity_id UUID UNIQUE, UNIQUE(tenant_id,id), UNIQUE(tenant_id,name),
 FOREIGN KEY(tenant_id,system_identity_id) REFERENCES managed_identities(tenant_id,id)
);
CREATE INDEX ix_managed_hosts_tenant ON managed_identity_hosts(tenant_id);
CREATE TABLE managed_identity_host_users (
 tenant_id UUID NOT NULL, host_id UUID NOT NULL, identity_id UUID NOT NULL,
 PRIMARY KEY(tenant_id,host_id,identity_id),
 FOREIGN KEY(tenant_id,host_id) REFERENCES managed_identity_hosts(tenant_id,id),
 FOREIGN KEY(tenant_id,identity_id) REFERENCES managed_identities(tenant_id,id)
);
""")


def downgrade():
    raise RuntimeError("Downgrade would discard managed identity bindings")
