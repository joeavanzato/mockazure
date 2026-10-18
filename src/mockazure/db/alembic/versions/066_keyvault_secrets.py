"""Encrypted versioned Key Vault secret data."""

from alembic import op

revision = "066"
down_revision = "065"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE keyvault_secrets (
 tenant_id uuid NOT NULL,id uuid NOT NULL,vault_id varchar(2048) NOT NULL,
 name varchar(127) NOT NULL,name_key varchar(127) NOT NULL,revision bigint NOT NULL,
 deleted_at timestamptz,purge_at timestamptz,
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,vault_id,name_key),
 FOREIGN KEY(tenant_id,vault_id) REFERENCES keyvault_vaults(tenant_id,scope_key)
 ON DELETE CASCADE);
CREATE TABLE keyvault_secret_versions (
 tenant_id uuid NOT NULL,id uuid NOT NULL,secret_id uuid NOT NULL,sequence bigint NOT NULL,
 encrypted_value text,attributes jsonb NOT NULL,tags jsonb NOT NULL,
 content_type varchar(255),created_at timestamptz NOT NULL,updated_at timestamptz NOT NULL,
 PRIMARY KEY(tenant_id,id),UNIQUE(tenant_id,secret_id,sequence),
 FOREIGN KEY(tenant_id,secret_id) REFERENCES keyvault_secrets(tenant_id,id) ON DELETE CASCADE);
CREATE TABLE keyvault_secret_cursors (
 tenant_id uuid NOT NULL,id uuid NOT NULL,vault_id varchar(2048) NOT NULL,
 generation bigint NOT NULL,query jsonb NOT NULL,last_key varchar(127) NOT NULL,
 expires_at timestamptz NOT NULL,PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,vault_id) REFERENCES keyvault_vaults(tenant_id,scope_key)
 ON DELETE CASCADE);
CREATE INDEX keyvault_secret_cursor_cleanup ON keyvault_secret_cursors(tenant_id,expires_at,id);
CREATE INDEX keyvault_secret_purge ON keyvault_secrets(tenant_id,purge_at,id);
""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM keyvault_secrets)
 THEN RAISE EXCEPTION 'Export and reset secret state before downgrade'; END IF; END $$;
DROP TABLE keyvault_secret_cursors,keyvault_secret_versions,keyvault_secrets;""")
