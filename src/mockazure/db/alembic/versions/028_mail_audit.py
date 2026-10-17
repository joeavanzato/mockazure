"""Allow UAL observations and index their bounded service/query dimensions."""

from alembic import op

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE evidence_observations DROP CONSTRAINT evidence_observations_family_check;
ALTER TABLE evidence_observations ADD CONSTRAINT evidence_observations_family_check
 CHECK(family IN ('signIn','directoryAudit','riskDetection','riskyUser','auditLogRecord'));
CREATE INDEX evidence_ual_service_idx ON evidence_versions
 (tenant_id, (payload->>'service'),event_time,record_id) WHERE family='auditLogRecord';
CREATE INDEX evidence_ual_operation_idx ON evidence_versions
 (tenant_id, (payload->>'operation'),event_time,record_id) WHERE family='auditLogRecord';
""")


def downgrade():
    op.execute("""
DROP INDEX evidence_ual_operation_idx, evidence_ual_service_idx;
ALTER TABLE evidence_observations DROP CONSTRAINT evidence_observations_family_check;
ALTER TABLE evidence_observations ADD CONSTRAINT evidence_observations_family_check
 CHECK(family IN ('signIn','directoryAudit','riskDetection','riskyUser'));
""")
