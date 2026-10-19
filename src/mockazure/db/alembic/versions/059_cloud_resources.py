"""Related operational provider resources, vaults and workspace identities."""

from alembic import op

revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE cloud_resources (
        tenant_id uuid NOT NULL, scope_key varchar(2048) NOT NULL, family varchar(32) NOT NULL,
        PRIMARY KEY(tenant_id,scope_key), FOREIGN KEY(tenant_id,scope_key)
            REFERENCES arm_resources(tenant_id,scope_key) ON DELETE CASCADE
    );
    CREATE TABLE cloud_resource_relationships (
        tenant_id uuid NOT NULL, source_key varchar(2048) NOT NULL,
        path varchar(1024) NOT NULL, target_key varchar(2048) NOT NULL,
        PRIMARY KEY(tenant_id,source_key,path),
        CONSTRAINT ck_cloud_relationship_subscription
            CHECK (split_part(source_key, '/', 3) = split_part(target_key, '/', 3)),
        FOREIGN KEY(tenant_id,source_key) REFERENCES arm_resources(tenant_id,scope_key)
            ON DELETE CASCADE,
        FOREIGN KEY(tenant_id,target_key) REFERENCES arm_resources(tenant_id,scope_key)
            DEFERRABLE INITIALLY DEFERRED
    );
    CREATE INDEX ix_cloud_relationship_target ON cloud_resource_relationships(tenant_id,target_key);
    CREATE TABLE keyvault_vaults (
        tenant_id uuid NOT NULL, scope_key varchar(2048) NOT NULL, name varchar(24) NOT NULL UNIQUE,
        PRIMARY KEY(tenant_id,scope_key), FOREIGN KEY(tenant_id,scope_key)
            REFERENCES arm_resources(tenant_id,scope_key) ON DELETE CASCADE
    );
    CREATE TABLE monitor_workspaces (
        tenant_id uuid NOT NULL, scope_key varchar(2048) NOT NULL, customer_id uuid NOT NULL UNIQUE,
        PRIMARY KEY(tenant_id,scope_key), FOREIGN KEY(tenant_id,scope_key)
            REFERENCES arm_resources(tenant_id,scope_key) ON DELETE CASCADE
    );
    """)


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM cloud_resources)
      OR EXISTS(SELECT 1 FROM cloud_resource_relationships)
      OR EXISTS(SELECT 1 FROM keyvault_vaults) OR EXISTS(SELECT 1 FROM monitor_workspaces)
      THEN RAISE EXCEPTION 'Cannot discard operational provider resources'; END IF; END $$;""")
    op.execute(
        "DROP TABLE monitor_workspaces; DROP TABLE keyvault_vaults; "
        "DROP TABLE cloud_resource_relationships; DROP TABLE cloud_resources;"
    )
