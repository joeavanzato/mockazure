"""Generation-scoped versions and legacy capture barriers for bounded resets."""

from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE evidence_state ADD COLUMN legacy_directory_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE evidence_state ADD COLUMN legacy_auth_cutoff_at timestamptz;
ALTER TABLE evidence_versions ADD COLUMN generation uuid;
ALTER TABLE evidence_versions ADD COLUMN source_kind varchar(40);
ALTER TABLE evidence_versions ADD COLUMN source_id uuid;
UPDATE evidence_versions v SET generation=s.generation,
 source_kind=o.source_kind,source_id=o.source_id
 FROM evidence_observations o JOIN evidence_sources s
 ON s.tenant_id=o.tenant_id AND s.kind=o.source_kind AND s.id=o.source_id
 WHERE v.tenant_id=o.tenant_id AND v.family=o.family AND v.record_id=o.record_id;
ALTER TABLE evidence_versions ALTER COLUMN generation SET NOT NULL;
ALTER TABLE evidence_versions ALTER COLUMN source_kind SET NOT NULL;
ALTER TABLE evidence_versions ALTER COLUMN source_id SET NOT NULL;
ALTER TABLE evidence_versions ADD CONSTRAINT fk_evidence_version_source
 FOREIGN KEY(tenant_id,source_kind,source_id) REFERENCES evidence_sources(tenant_id,kind,id);
DROP INDEX uq_evidence_current;
CREATE UNIQUE INDEX uq_evidence_current ON evidence_versions(tenant_id,generation,family,record_id)
 WHERE valid_to IS NULL;
CREATE INDEX ix_evidence_generation
 ON evidence_versions(tenant_id,generation,family,event_time,record_id);
""")


def downgrade():
    raise RuntimeError("Downgrade would discard generation and provenance guarantees")
