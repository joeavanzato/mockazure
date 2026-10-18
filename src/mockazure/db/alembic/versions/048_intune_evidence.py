"""Index observed device identity and register the shared Intune read budget."""

from alembic import op

revision = "048"
down_revision = "047"
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
    "'drives',"
    "'teams',"
    "'chats'"
)
NEW = OLD + ",'deviceManagement'"


def upgrade():
    op.execute("""CREATE INDEX intune_directory_device ON workload_versions
        (tenant_id, lower(payload->>'azureADDeviceId'))
        WHERE workload='intune' AND kind='managedDevice' AND valid_to IS NULL AND NOT deleted""")
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_resource_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_resource_check CHECK(resource IN ({NEW}))"
        )


def downgrade():
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM workload_scopes WHERE workload='intune')
        OR EXISTS(SELECT 1 FROM graph_request_budgets WHERE resource='deviceManagement')
        OR EXISTS(SELECT 1 FROM graph_read_faults WHERE resource='deviceManagement') THEN
        RAISE EXCEPTION 'Export and clear Intune evidence and budgets before downgrade';
        END IF; END $$;
        DROP INDEX intune_directory_device;""")
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_resource_check")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_resource_check CHECK(resource IN ({OLD}))"
        )
