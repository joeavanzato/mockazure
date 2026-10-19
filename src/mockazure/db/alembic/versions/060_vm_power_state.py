"""Persistent simulated VM power state, scoped to canonical resources."""

from alembic import op

revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE cloud_vm_states (
        tenant_id uuid NOT NULL,scope_key varchar(2048) NOT NULL,
        power_state varchar(24) NOT NULL DEFAULT 'running',
        PRIMARY KEY(tenant_id,scope_key),
        FOREIGN KEY(tenant_id,scope_key) REFERENCES cloud_resources(tenant_id,scope_key)
            ON DELETE CASCADE,
        CHECK(power_state IN ('running','stopped','deallocated')));
        INSERT INTO cloud_vm_states(tenant_id,scope_key)
        SELECT tenant_id,scope_key FROM cloud_resources WHERE family='vm';""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM jobs
        WHERE kind='arm.provider.operation' AND state IN ('pending','running'))
        OR EXISTS(SELECT 1 FROM cloud_vm_states WHERE power_state<>'running')
        THEN RAISE EXCEPTION 'Cannot discard active provider operation or VM state';
        END IF; END $$;""")
    op.drop_table("cloud_vm_states")
