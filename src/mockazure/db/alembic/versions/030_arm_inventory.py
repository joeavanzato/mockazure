"""Canonical ARM inventory and inherited roles, preserving legacy Reader IDs."""

from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE arm_subscriptions ADD COLUMN tags JSONB NOT NULL DEFAULT '{}',
 ADD COLUMN revision BIGINT NOT NULL DEFAULT 0;
CREATE TABLE arm_resources (
 tenant_id UUID NOT NULL, subscription_id UUID NOT NULL, scope_key VARCHAR(2048) NOT NULL,
 display_id VARCHAR(2048) NOT NULL, kind VARCHAR(16) NOT NULL, group_key VARCHAR(2048),
 name VARCHAR(1024) NOT NULL, resource_type VARCHAR(512) NOT NULL, location VARCHAR(100) NOT NULL,
 tags JSONB NOT NULL, payload JSONB NOT NULL, provisioning_state VARCHAR(32) NOT NULL,
 revision BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(tenant_id,scope_key), UNIQUE(tenant_id,subscription_id,scope_key),
 FOREIGN KEY(tenant_id,subscription_id) REFERENCES arm_subscriptions(tenant_id,id),
 FOREIGN KEY(tenant_id,subscription_id,group_key)
   REFERENCES arm_resources(tenant_id,subscription_id,scope_key) ON DELETE CASCADE,
 CHECK(kind IN ('group','resource')), CHECK(scope_key=lower(scope_key)),
 CHECK(left(scope_key,length(subscription_id::text)+16)=
       '/subscriptions/' || subscription_id::text || '/'),
 CHECK((kind='group' AND group_key IS NULL) OR (kind='resource' AND group_key IS NOT NULL
       AND left(scope_key,length(group_key)+11)=group_key || '/providers/'))
);
CREATE INDEX ix_arm_inventory ON arm_resources(tenant_id,subscription_id,kind,scope_key);
CREATE INDEX ix_arm_inventory_type
 ON arm_resources(tenant_id,subscription_id,resource_type,scope_key);
ALTER TABLE arm_reader_assignments DROP CONSTRAINT arm_reader_assignments_check;
ALTER TABLE arm_reader_assignments RENAME TO arm_role_assignments;
ALTER TABLE arm_role_assignments ADD COLUMN group_id UUID,
 ADD COLUMN scope_key VARCHAR(2048), ADD COLUMN inventory_key VARCHAR(2048),
 ADD COLUMN role_id VARCHAR(36) NOT NULL DEFAULT 'acdd72a7-3385-48ef-bd42-f606fba81ae7',
 ADD COLUMN legacy BOOLEAN NOT NULL DEFAULT false,
 ADD COLUMN description VARCHAR(1024), ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE arm_role_assignments SET scope_key='/subscriptions/' || subscription_id::text, legacy=true;
ALTER TABLE arm_role_assignments ALTER COLUMN scope_key SET NOT NULL,
 ADD FOREIGN KEY(tenant_id,group_id) REFERENCES directory_groups(tenant_id,id),
 ADD FOREIGN KEY(tenant_id,subscription_id,inventory_key)
   REFERENCES arm_resources(tenant_id,subscription_id,scope_key) ON DELETE CASCADE,
 ADD CHECK(num_nonnulls(client_id,user_id,group_id)=1),
 ADD CHECK((inventory_key IS NULL AND scope_key='/subscriptions/' || subscription_id::text)
   OR (inventory_key IS NOT NULL AND scope_key=inventory_key));
CREATE UNIQUE INDEX uq_arm_assignment ON arm_role_assignments
 (tenant_id,scope_key,role_id,client_id,user_id,group_id) NULLS NOT DISTINCT WHERE NOT legacy;
CREATE INDEX ix_arm_assignment_scope ON arm_role_assignments(tenant_id,scope_key,role_id);
""")


def downgrade():
    # Never silently discard scoped/group/non-Reader grants on downgrade.
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM arm_resources) OR EXISTS(
 SELECT 1 FROM arm_role_assignments WHERE group_id IS NOT NULL OR inventory_key IS NOT NULL
 OR role_id <> 'acdd72a7-3385-48ef-bd42-f606fba81ae7') THEN
 RAISE EXCEPTION 'M5 scoped/non-Reader assignments prevent downgrade'; END IF;
END $$;
DROP TABLE arm_resources CASCADE;
DROP INDEX uq_arm_assignment;
DROP INDEX ix_arm_assignment_scope;
ALTER TABLE arm_role_assignments DROP COLUMN group_id, DROP COLUMN scope_key,
 DROP COLUMN inventory_key, DROP COLUMN legacy, DROP COLUMN role_id,
 DROP COLUMN description, DROP COLUMN created_at;
ALTER TABLE arm_role_assignments ADD CONSTRAINT arm_reader_assignments_check
 CHECK((client_id IS NULL) <> (user_id IS NULL));
ALTER TABLE arm_role_assignments RENAME TO arm_reader_assignments;
ALTER TABLE arm_subscriptions DROP COLUMN tags, DROP COLUMN revision;
""")
