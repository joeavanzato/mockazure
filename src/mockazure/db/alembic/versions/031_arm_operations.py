"""Tenant-owned durable resource-group deletion linkage."""

from alembic import op

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE jobs ADD CONSTRAINT uq_jobs_tenant_id UNIQUE (tenant_id,id)")
    op.execute("ALTER TABLE arm_resources ADD COLUMN deletion_job_id uuid")
    op.execute("""
        ALTER TABLE arm_resources ADD CONSTRAINT fk_arm_deletion_job
        FOREIGN KEY (tenant_id,deletion_job_id) REFERENCES jobs(tenant_id,id)
    """)


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM arm_resources WHERE deletion_job_id IS NOT NULL) THEN
                RAISE EXCEPTION 'Active ARM operations prevent downgrade';
            END IF;
        END $$
    """)
    op.execute("ALTER TABLE arm_resources DROP COLUMN deletion_job_id")
    op.execute("ALTER TABLE jobs DROP CONSTRAINT uq_jobs_tenant_id")
