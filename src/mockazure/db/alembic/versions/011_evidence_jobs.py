"""Tenant evidence facts, snapshots and monotonic job fences."""

from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE evidence_cursors (
 token_hash VARCHAR(64) NOT NULL,
 tenant_id UUID NOT NULL,
 generation UUID NOT NULL,
 revision BIGINT NOT NULL,
 evidence_time TIMESTAMP WITH TIME ZONE NOT NULL,
 retention_cutoff TIMESTAMP WITH TIME ZONE NOT NULL,
 expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
 state JSONB NOT NULL,
 PRIMARY KEY (token_hash),
 FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_evidence_cursor_expiry ON evidence_cursors (tenant_id, expires_at);
CREATE TABLE evidence_risk_cutoffs (
 tenant_id UUID NOT NULL,
 user_id UUID NOT NULL,
 generation UUID NOT NULL,
 occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
 source_revision BIGINT NOT NULL,
 action VARCHAR(32) NOT NULL,
 payload JSONB NOT NULL,
 PRIMARY KEY (tenant_id, user_id),
 FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE TABLE evidence_sources (
 tenant_id UUID NOT NULL,
 kind VARCHAR(40) NOT NULL,
 id UUID NOT NULL,
 generation UUID NOT NULL,
 source_revision BIGINT NOT NULL,
 occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
 available_at TIMESTAMP WITH TIME ZONE NOT NULL,
 facts JSONB NOT NULL,
 content_hash VARCHAR(64) NOT NULL,
 acknowledged_at TIMESTAMP WITH TIME ZONE,
 PRIMARY KEY (tenant_id, kind, id),
 UNIQUE (tenant_id, source_revision),
 CHECK (available_at >= occurred_at AND source_revision > 0),
 FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_evidence_sources_pending ON evidence_sources (tenant_id, source_revision) WHERE
 acknowledged_at IS NULL;
CREATE TABLE evidence_state (
 tenant_id UUID NOT NULL,
 generation UUID NOT NULL,
 source_revision BIGINT NOT NULL,
 publication_revision BIGINT NOT NULL,
 settings JSONB NOT NULL,
 PRIMARY KEY (tenant_id),
 FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE TABLE evidence_observations (
 tenant_id UUID NOT NULL,
 family VARCHAR(24) NOT NULL,
 record_id UUID NOT NULL,
 source_kind VARCHAR(40) NOT NULL,
 source_id UUID NOT NULL,
 occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
 available_at TIMESTAMP WITH TIME ZONE NOT NULL,
 subject_id UUID,
 payload JSONB NOT NULL,
 PRIMARY KEY (tenant_id, family, record_id),
 FOREIGN KEY(tenant_id, source_kind, source_id) REFERENCES evidence_sources (tenant_id, kind, id),
 CHECK (family IN ('signIn','directoryAudit','riskDetection','riskyUser')),
 CHECK (available_at >= occurred_at)
);
CREATE TABLE evidence_projection_receipts (
 tenant_id UUID NOT NULL,
 source_kind VARCHAR(40) NOT NULL,
 source_id UUID NOT NULL,
 generation UUID NOT NULL,
 revision BIGINT NOT NULL,
 status VARCHAR(24) NOT NULL,
 record_ids JSONB NOT NULL,
 PRIMARY KEY (tenant_id, source_kind, source_id),
 FOREIGN KEY(tenant_id, source_kind, source_id) REFERENCES evidence_sources (tenant_id, kind, id)
);
CREATE TABLE evidence_versions (
 tenant_id UUID NOT NULL,
 family VARCHAR(24) NOT NULL,
 record_id UUID NOT NULL,
 valid_from BIGINT NOT NULL,
 valid_to BIGINT,
 event_time TIMESTAMP WITH TIME ZONE NOT NULL,
 available_at TIMESTAMP WITH TIME ZONE NOT NULL,
 subject_id UUID,
 app_id VARCHAR(64),
 event_type VARCHAR(40),
 payload JSONB NOT NULL,
 PRIMARY KEY (tenant_id, family, record_id, valid_from),
FOREIGN KEY(tenant_id, family, record_id) REFERENCES evidence_observations (tenant_id, family,
 record_id),
 CHECK (valid_from > 0 AND (valid_to IS NULL OR valid_to > valid_from))
);
CREATE INDEX ix_evidence_app ON evidence_versions (tenant_id, family, app_id, event_time,
 record_id);
CREATE INDEX ix_evidence_time ON evidence_versions (tenant_id, family, event_time, record_id);
CREATE INDEX ix_evidence_type ON evidence_versions (tenant_id, family, event_type, event_time,
 record_id);
CREATE INDEX ix_evidence_user ON evidence_versions (tenant_id, family, subject_id, event_time,
 record_id);
CREATE UNIQUE INDEX uq_evidence_current ON evidence_versions (tenant_id, family, record_id)
 WHERE valid_to IS NULL;
ALTER TABLE jobs ADD COLUMN claim_epoch bigint NOT NULL DEFAULT 0 CHECK(claim_epoch >= 0);
ALTER TABLE jobs ADD COLUMN source_key varchar(100);
CREATE UNIQUE INDEX uq_jobs_source ON jobs(tenant_id,kind,source_key) WHERE source_key IS NOT NULL;
""")


def downgrade():
    op.execute("""
DROP INDEX uq_jobs_source;
ALTER TABLE jobs DROP COLUMN source_key;
ALTER TABLE jobs DROP COLUMN claim_epoch;
DROP TABLE evidence_versions;
DROP TABLE evidence_projection_receipts;
DROP TABLE evidence_observations;
DROP TABLE evidence_state;
DROP TABLE evidence_sources;
DROP TABLE evidence_risk_cutoffs;
DROP TABLE evidence_cursors;
""")
