"""Persistent browser transactions, delegated sessions and refresh handles."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def principals():
    return [
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
    ]


def upgrade():
    op.create_table(
        "oauth_authorizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("common", sa.Boolean(), nullable=False),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False),
        sa.Column("resource", sa.String(30), nullable=False),
        sa.Column("state", sa.String(2048), nullable=False),
        sa.Column("nonce", sa.String(2048), nullable=False),
        sa.Column("challenge", sa.String(128), nullable=False),
        sa.Column("response_mode", sa.String(20), nullable=False),
        sa.Column("cookie_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("mfa", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code_hash", sa.String(64), unique=True),
        sa.Column("code_expires_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        *principals(),
    )
    op.create_index("ix_oauth_authorizations_expires_at", "oauth_authorizations", ["expires_at"])
    op.create_table(
        "oauth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("resource", sa.String(30), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False),
        sa.Column("mfa", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "id"),
        *principals(),
    )
    op.create_index("ix_oauth_sessions_tenant_id", "oauth_sessions", ["tenant_id"])
    op.create_index("ix_oauth_sessions_expires_at", "oauth_sessions", ["expires_at"])
    op.create_table(
        "oauth_refresh_handles",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"], ["oauth_sessions.tenant_id", "oauth_sessions.id"]
        ),
    )
    op.create_index("ix_oauth_refresh_handles_session_id", "oauth_refresh_handles", ["session_id"])
    op.create_index("ix_oauth_refresh_handles_expires_at", "oauth_refresh_handles", ["expires_at"])


def downgrade():
    op.drop_table("oauth_refresh_handles")
    op.drop_table("oauth_sessions")
    op.drop_table("oauth_authorizations")
