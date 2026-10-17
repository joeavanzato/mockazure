"""Preserve the established client category when refreshing a local session."""

from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE oauth_sessions ADD COLUMN client_app_type varchar(40) NOT NULL DEFAULT 'browser';
UPDATE oauth_sessions s SET client_app_type='mobileAppsAndDesktopClients'
FROM identity_applications a WHERE a.client_id=s.client_id AND a.client_mode='public';
ALTER TABLE oauth_sessions ADD CONSTRAINT oauth_session_client_category
CHECK (client_app_type IN ('browser','mobileAppsAndDesktopClients'));
""")


def downgrade():
    op.execute("ALTER TABLE oauth_sessions DROP COLUMN client_app_type")
