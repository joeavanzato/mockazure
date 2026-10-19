"""Retain compact sign-in attempt references for the lifetime of a user."""

from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE security_user_signin_activity (
 tenant_id uuid NOT NULL, record_id uuid NOT NULL, user_id uuid NOT NULL,
 occurred_at timestamptz NOT NULL, available_at timestamptz NOT NULL,
 interactive boolean NOT NULL, successful boolean NOT NULL,
 PRIMARY KEY (tenant_id, record_id),
 FOREIGN KEY (tenant_id,user_id) REFERENCES identity_users(tenant_id,id) ON DELETE CASCADE
);
CREATE INDEX security_user_signin_attempt_idx ON security_user_signin_activity
 (tenant_id,user_id,interactive,occurred_at DESC);
CREATE INDEX security_user_signin_success_idx ON security_user_signin_activity
 (tenant_id,user_id,occurred_at DESC) WHERE successful;
INSERT INTO security_user_signin_activity
SELECT e.tenant_id,e.record_id,e.subject_id,e.occurred_at,e.available_at,
 (e.payload->>'isInteractive')::boolean, (e.payload->'status'->>'errorCode')::integer=0
FROM evidence_observations e JOIN identity_users u
 ON e.tenant_id=u.tenant_id AND e.subject_id=u.id
WHERE e.family='signIn' AND u.deleted_at IS NULL
 AND e.payload->>'isInteractive' IN ('true','false')
 AND e.payload->'status'->>'errorCode' ~ '^[0-9]+$';
""")


def downgrade():
    op.execute("DROP TABLE security_user_signin_activity")
