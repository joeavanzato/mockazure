"""Bound per-user enrollment lookup at large tenant sizes."""

from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE INDEX ix_security_methods_user ON security_objects
(tenant_id, (properties->>'userId'), id)
WHERE kind = 'authenticationMethod';
""")


def downgrade():
    op.execute("DROP INDEX ix_security_methods_user")
