"""Durable security collection evidence and snapshot continuations."""

from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE collection_states (
 tenant_id UUID PRIMARY KEY REFERENCES tenants(id), revision BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE collection_records (
 tenant_id UUID NOT NULL REFERENCES tenants(id), family VARCHAR(40) NOT NULL,
 record_id VARCHAR(256) NOT NULL, valid_from BIGINT NOT NULL, valid_to BIGINT,
 payload JSONB NOT NULL, times JSONB NOT NULL,
 PRIMARY KEY (tenant_id,family,record_id,valid_from),
 CHECK (valid_to IS NULL OR valid_to > valid_from),
 CHECK (family IN ('alerts','alerts_v2','incidents','secureScores','secureScoreControlProfiles'))
);
CREATE UNIQUE INDEX collection_current_idx ON collection_records(tenant_id,family,record_id)
 WHERE valid_to IS NULL;
CREATE INDEX collection_created_idx ON collection_records
 (tenant_id,family,((times->>'createdDateTime')::bigint) DESC,record_id);
CREATE INDEX collection_incident_idx ON collection_records
 (tenant_id,family,((payload->>'incidentId')),valid_from);
CREATE TABLE collection_cursors (
 token_hash VARCHAR(64) PRIMARY KEY, tenant_id UUID NOT NULL REFERENCES tenants(id),
 revision BIGINT NOT NULL, expires_at TIMESTAMPTZ NOT NULL, state JSONB NOT NULL
);
CREATE INDEX collection_cursor_expiry_idx ON collection_cursors(expires_at);
""")


def downgrade():
    op.execute("DROP TABLE collection_cursors, collection_records, collection_states")
