"""Retain deleted vaults and separate public secret versions from row identity."""

from alembic import op

revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE keyvault_secret_versions ADD COLUMN version varchar(32);
UPDATE keyvault_secret_versions SET version=replace(id::text,'-','');
ALTER TABLE keyvault_secret_versions ALTER COLUMN version SET NOT NULL;
ALTER TABLE keyvault_secret_versions ADD CONSTRAINT keyvault_public_secret_version
 UNIQUE(tenant_id,secret_id,version);
ALTER TABLE keyvault_secrets DROP CONSTRAINT keyvault_secrets_tenant_id_vault_id_fkey;
ALTER TABLE keyvault_secrets ADD CONSTRAINT keyvault_secret_tenant
 FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE;
CREATE TABLE keyvault_deleted_vaults (
 tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
 vault_id varchar(2048) NOT NULL,name varchar(24) NOT NULL UNIQUE,
 display_id varchar(2048) NOT NULL,location varchar(100) NOT NULL,
 tags jsonb NOT NULL,payload jsonb NOT NULL,deleted_at timestamptz NOT NULL,
 purge_at timestamptz NOT NULL,PRIMARY KEY(tenant_id,vault_id));
CREATE INDEX keyvault_deleted_vault_cleanup ON keyvault_deleted_vaults(tenant_id,purge_at,vault_id);
""")


def downgrade():
    op.execute("""DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM keyvault_secrets) OR EXISTS(SELECT 1 FROM keyvault_deleted_vaults)
 THEN RAISE EXCEPTION 'Export and reset secret state before downgrade'; END IF; END $$;
DROP TABLE keyvault_deleted_vaults;
ALTER TABLE keyvault_secrets DROP CONSTRAINT keyvault_secret_tenant;
ALTER TABLE keyvault_secrets ADD CONSTRAINT keyvault_secrets_tenant_id_vault_id_fkey
 FOREIGN KEY(tenant_id,vault_id) REFERENCES keyvault_vaults(tenant_id,scope_key) ON DELETE CASCADE;
ALTER TABLE keyvault_secret_versions DROP CONSTRAINT keyvault_public_secret_version;
ALTER TABLE keyvault_secret_versions DROP COLUMN version;""")
