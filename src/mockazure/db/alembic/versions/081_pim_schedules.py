"""Separate PIM eligibility, activation schedules, requests and policy state."""

from alembic import op

revision = "081"
down_revision = "080"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE security_pim_schedules (
 tenant_id uuid NOT NULL REFERENCES tenants(id), id uuid NOT NULL,
 principal_id uuid NOT NULL, role varchar(200) NOT NULL, kind varchar(20) NOT NULL,
 starts_at timestamptz NOT NULL, ends_at timestamptz, revoked_at timestamptz,
 eligibility_id uuid, payload jsonb NOT NULL, PRIMARY KEY(tenant_id,id),
 CHECK(kind IN ('eligibility','assignment')),
 CHECK(ends_at IS NULL OR ends_at > starts_at),
 FOREIGN KEY(tenant_id,eligibility_id) REFERENCES security_pim_schedules(tenant_id,id)
);
CREATE INDEX ix_pim_effective ON security_pim_schedules
 (tenant_id,principal_id,kind,starts_at,ends_at);
CREATE TABLE security_pim_requests (
 tenant_id uuid NOT NULL REFERENCES tenants(id), id uuid NOT NULL,
 principal_id uuid NOT NULL, kind varchar(20) NOT NULL, schedule_id uuid,
 payload jsonb NOT NULL, PRIMARY KEY(tenant_id,id),
 FOREIGN KEY(tenant_id,schedule_id) REFERENCES security_pim_schedules(tenant_id,id)
);
CREATE INDEX ix_pim_request_principal ON security_pim_requests(tenant_id,principal_id,kind);
CREATE TABLE security_pim_policies (
 tenant_id uuid NOT NULL REFERENCES tenants(id), role varchar(200) NOT NULL,
 rules jsonb NOT NULL, PRIMARY KEY(tenant_id,role)
);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM security_pim_requests) OR EXISTS(SELECT 1 FROM security_pim_schedules)
 OR EXISTS(SELECT 1 FROM security_pim_policies)
 THEN RAISE EXCEPTION 'Cannot discard PIM state'; END IF;
END $$;
DROP TABLE security_pim_requests;
DROP TABLE security_pim_schedules;
DROP TABLE security_pim_policies;
""")
