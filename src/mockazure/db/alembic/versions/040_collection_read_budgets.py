"""Extend durable read budgets to the declared collection profiles."""

from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None

OLD = "'users','groups','signIn','directoryAudit','riskDetection','riskyUser'"
NEW = (
    OLD
    + ","
    + ",".join(
        "'" + name + "'"
        for name in (
            "applications",
            "servicePrincipals",
            "devices",
            "directoryObjects",
            "organization",
            "subscribedSkus",
            "directoryRoles",
            "roleManagement",
            "oauth2PermissionGrants",
            "conditionalAccess",
            "userRegistrationDetails",
            "security.alerts",
            "security.alerts_v2",
            "security.incidents",
            "security.secureScores",
            "security.secureScoreControlProfiles",
        )
    )
)


def upgrade():
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_resource_check")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN resource TYPE varchar(64)")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_resource_check CHECK(resource IN ({NEW}))"
        )


def downgrade():
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM {table} WHERE resource NOT IN ({OLD})) THEN
          RAISE EXCEPTION 'Remove new collection budgets and faults before downgrade';
        END IF;
        END $$;""")
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_resource_check")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN resource TYPE varchar(16)")
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_resource_check CHECK(resource IN ({OLD}))"
        )
