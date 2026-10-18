"""Authoritative Teams resource consent and operation read budgets."""

from alembic import op

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None

OLD = (
    "'users',"
    "'groups',"
    "'signIn',"
    "'directoryAudit',"
    "'riskDetection',"
    "'riskyUser',"
    "'applications',"
    "'servicePrincipals',"
    "'devices',"
    "'directoryObjects',"
    "'organization',"
    "'subscribedSkus',"
    "'directoryRoles',"
    "'roleManagement',"
    "'oauth2PermissionGrants',"
    "'conditionalAccess',"
    "'userRegistrationDetails',"
    "'security.alerts',"
    "'security.alerts_v2',"
    "'security.incidents',"
    "'security.secureScores',"
    "'security.secureScoreControlProfiles',"
    "'sites',"
    "'drives'"
)
NEW = OLD + ",'teams','chats'"


def upgrade():
    op.execute("""
CREATE TABLE conversation_access (
 id uuid PRIMARY KEY,tenant_id uuid NOT NULL REFERENCES tenants(id),scope varchar(512) NOT NULL,
 resource_kind varchar(32) NOT NULL CHECK(resource_kind IN ('team','channel','chat')),
 resource_id varchar(512) NOT NULL,principal_kind varchar(16) NOT NULL
 CHECK(principal_kind IN ('user','application')),principal_id uuid NOT NULL,
 permission varchar(64) NOT NULL,
 UNIQUE(tenant_id,scope,resource_kind,resource_id,principal_kind,principal_id,permission)
);
CREATE INDEX conversation_principal ON conversation_access
 (tenant_id,principal_kind,principal_id,scope,permission);
DROP INDEX workload_global_identity;
CREATE UNIQUE INDEX workload_global_identity ON workload_versions
 (tenant_id,workload,kind,id) WHERE valid_to IS NULL AND kind IN ('site','drive','team','chat');
""")
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_resource_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_resource_check CHECK(resource IN ({NEW}))"
        )


def downgrade():
    op.execute("""DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM workload_scopes WHERE workload='teams')
 OR EXISTS(SELECT 1 FROM conversation_access)
 OR EXISTS(SELECT 1 FROM graph_request_budgets WHERE resource IN ('teams','chats'))
 OR EXISTS(SELECT 1 FROM graph_read_faults WHERE resource IN ('teams','chats')) THEN
 RAISE EXCEPTION 'Export and clear Teams evidence, authority and budgets before downgrade';
 END IF; END $$;
DROP TABLE conversation_access;
DROP INDEX workload_global_identity;
CREATE UNIQUE INDEX workload_global_identity ON workload_versions
 (tenant_id,workload,kind,id) WHERE valid_to IS NULL AND kind IN ('site','drive');
""")
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_resource_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_resource_check CHECK(resource IN ({OLD}))"
        )
