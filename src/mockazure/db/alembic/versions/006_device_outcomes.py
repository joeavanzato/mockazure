"""Device polling and bounded, non-secret authentication outcome records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "oauth_devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("common", sa.Boolean(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False, unique=True),
        sa.Column("user_code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("resource", sa.String(30), nullable=False),
        sa.Column("scopes", JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("mfa", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column("last_poll", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
    )
    op.create_index("ix_oauth_devices_expires_at", "oauth_devices", ["expires_at"])
    op.add_column("oauth_authorizations", sa.Column("device_id", sa.Uuid()))
    op.create_foreign_key(
        "fk_authorization_device", "oauth_authorizations", "oauth_devices", ["device_id"], ["id"]
    )
    op.create_table(
        "identity_authentication_outcomes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid()),
        sa.Column("flow", sa.String(30), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("result", sa.String(40), nullable=False),
        sa.Column("mfa", sa.String(20), nullable=False),
    )
    op.create_index(
        "ix_identity_authentication_outcomes_tenant_id",
        "identity_authentication_outcomes",
        ["tenant_id"],
    )
    op.create_index(
        "ix_identity_authentication_outcomes_timestamp",
        "identity_authentication_outcomes",
        ["timestamp"],
    )


def downgrade():
    op.drop_table("identity_authentication_outcomes")
    op.drop_column("oauth_authorizations", "device_id")
    op.drop_table("oauth_devices")
