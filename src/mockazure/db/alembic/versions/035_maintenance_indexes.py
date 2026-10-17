
"""Index bounded expired-query and closed-rule-history reclamation."""

from alembic import op

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE INDEX audit_query_expiry_idx ON audit_queries (tenant_id, expires_at, id);
CREATE INDEX mail_rule_closed_idx ON mail_rule_versions (tenant_id, user_id, id, valid_from)
 WHERE valid_to IS NOT NULL;
CREATE INDEX directory_cursor_resource_idx ON directory_continuations
 (tenant_id, (state #>> '{binding,resource}'), expires_at);
""")


def downgrade():
    op.drop_index("directory_cursor_resource_idx", table_name="directory_continuations")
    op.drop_index("mail_rule_closed_idx", table_name="mail_rule_versions")
    op.drop_index("audit_query_expiry_idx", table_name="audit_queries")
