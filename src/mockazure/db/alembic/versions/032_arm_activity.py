"""Activity Log family with exact 100ns time and scoped SQL dimensions."""

from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None

COLUMNS = {
    "arm_subscription_id": "uuid",
    "arm_scope_key": "varchar(2048)",
    "arm_group_name": "varchar(90)",
    "arm_provider": "varchar(256)",
    "arm_correlation_id": "varchar(128)",
    "arm_event_ticks": "bigint",
    "arm_available_ticks": "bigint",
}


def upgrade():
    op.execute(
        "ALTER TABLE evidence_observations DROP CONSTRAINT evidence_observations_family_check"
    )
    op.execute("""
        ALTER TABLE evidence_observations ADD CONSTRAINT evidence_observations_family_check
        CHECK(family IN ('signIn','directoryAudit','riskDetection',
                         'riskyUser','auditLogRecord','activityLog'))
    """)
    for name, kind in COLUMNS.items():
        op.execute(f"ALTER TABLE evidence_versions ADD COLUMN {name} {kind}")
    op.execute("""
        ALTER TABLE evidence_versions ADD CONSTRAINT fk_activity_subscription
        FOREIGN KEY (tenant_id,arm_subscription_id) REFERENCES arm_subscriptions(tenant_id,id)
    """)
    columns = ",".join(COLUMNS)
    op.execute(f"""
        ALTER TABLE evidence_versions ADD CONSTRAINT ck_activity_dimensions
        CHECK((family='activityLog' AND num_nonnulls({columns})=7
               AND arm_event_ticks>0 AND arm_available_ticks>=arm_event_ticks)
              OR (family<>'activityLog' AND num_nonnulls({columns})=0))
    """)
    for suffix, extra in (
        ("time", ""),
        ("group", "arm_group_name,"),
        ("scope", "arm_scope_key,"),
        ("provider", "arm_provider,"),
        ("correlation", "arm_correlation_id,"),
    ):
        op.execute(f"""
            CREATE INDEX ix_arm_activity_{suffix} ON evidence_versions
            (tenant_id,arm_subscription_id,{extra}arm_event_ticks,record_id)
            WHERE family='activityLog'
        """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM evidence_observations WHERE family='activityLog') THEN
                RAISE EXCEPTION 'Activity evidence prevents downgrade';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE evidence_versions DROP CONSTRAINT ck_activity_dimensions")
    op.execute("ALTER TABLE evidence_versions DROP CONSTRAINT fk_activity_subscription")
    for suffix in ("time", "group", "scope", "provider", "correlation"):
        op.execute(f"DROP INDEX ix_arm_activity_{suffix}")
    for name in COLUMNS:
        op.execute(f"ALTER TABLE evidence_versions DROP COLUMN {name}")
    op.execute(
        "ALTER TABLE evidence_observations DROP CONSTRAINT evidence_observations_family_check"
    )
    op.execute("""
        ALTER TABLE evidence_observations ADD CONSTRAINT evidence_observations_family_check
        CHECK(family IN ('signIn','directoryAudit','riskDetection','riskyUser','auditLogRecord'))
    """)
