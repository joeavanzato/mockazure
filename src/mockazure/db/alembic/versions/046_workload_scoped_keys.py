"""Separate stable import keys from parent-scoped public identifiers."""

from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
DROP INDEX workload_current_identity;
CREATE UNIQUE INDEX workload_current_identity ON workload_versions
 (tenant_id,workload,scope,kind,id) WHERE valid_to IS NULL;
CREATE UNIQUE INDEX workload_global_identity ON workload_versions
 (tenant_id,workload,kind,id) WHERE valid_to IS NULL AND kind IN ('site','drive');
CREATE INDEX workload_public_identity ON workload_versions
 (tenant_id,workload,scope,kind,(payload->>'id')) WHERE valid_to IS NULL;
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM workload_versions WHERE NOT deleted AND id <> payload->>'id')
 OR EXISTS(SELECT 1 FROM workload_versions WHERE valid_to IS NULL
 GROUP BY tenant_id,workload,kind,id HAVING count(*)>1) THEN
 RAISE EXCEPTION 'Export and clear workload evidence with scoped keys before downgrade';
 END IF;
END $$;
DROP INDEX workload_public_identity,workload_global_identity,workload_current_identity;
CREATE UNIQUE INDEX workload_current_identity ON workload_versions
 (tenant_id,workload,kind,id) WHERE valid_to IS NULL;
""")
