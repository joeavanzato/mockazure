"""Persistent tenant-scoped principals, credentials and permission grants."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "identity_users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("upn", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("verifier", sa.String(256), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id"),
        sa.UniqueConstraint("tenant_id", "upn"),
    )
    op.create_table(
        "identity_applications",
        sa.Column("client_id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("client_mode", sa.String(20), nullable=False),
        sa.Column("redirect_uris", JSONB(), nullable=False),
        sa.Column("required_permissions", JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "client_id"),
        sa.CheckConstraint("client_mode IN ('public','confidential')"),
    )
    op.create_table(
        "identity_service_principals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column(
            "client_id", sa.Uuid(), sa.ForeignKey("identity_applications.client_id"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "client_id"),
    )
    op.create_table(
        "identity_credentials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("verifier", sa.String(256)),
        sa.Column("public_certificate", sa.Text()),
        sa.Column("thumbprint", sa.String(128)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_applications.tenant_id", "identity_applications.client_id"],
        ),
        sa.CheckConstraint("kind IN ('secret','certificate')"),
    )
    op.create_table(
        "identity_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("resource", sa.String(30), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("permissions", JSONB(), nullable=False),
        sa.Column("user_id", sa.Uuid()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "client_id",
            "resource",
            "kind",
            "user_id",
            postgresql_nulls_not_distinct=True,
        ),
        sa.CheckConstraint("kind IN ('application','delegated')"),
        sa.CheckConstraint("kind != 'application' OR user_id IS NULL"),
    )
    op.create_table(
        "identity_directory_roles",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("role", sa.String(80), primary_key=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
    )
    for table in ("users", "applications", "service_principals", "credentials", "grants"):
        op.create_index(f"ix_identity_{table}_tenant_id", f"identity_{table}", ["tenant_id"])


def downgrade():
    for table in (
        "directory_roles",
        "grants",
        "credentials",
        "service_principals",
        "applications",
        "users",
    ):
        op.drop_table(f"identity_{table}")
