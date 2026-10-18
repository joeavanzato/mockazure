"""Extend the persisted read-budget allowlist for qualified site/drive adapters."""

from alembic import op

revision = "045"
down_revision = "044"
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
    "'security.secureScoreControlProfiles'"
)
NEW = OLD + ",'sites','drives'"


def upgrade():
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_resource_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_resource_check CHECK(resource IN ({NEW}))"
        )


def downgrade():
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"""DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM {table} WHERE resource IN ('sites','drives')) THEN
            RAISE EXCEPTION 'Clear site/drive budgets and injected faults before downgrade';
          END IF;
        END $$;""")
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_resource_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_resource_check CHECK(resource IN ({OLD}))"
        )
