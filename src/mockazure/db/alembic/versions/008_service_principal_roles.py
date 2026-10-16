"""Tenant-owned directory roles for service principals."""

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "identity_service_principal_roles",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.Uuid(), primary_key=True),
        sa.Column("role", sa.String(80), primary_key=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
    )


def downgrade():
    op.drop_table("identity_service_principal_roles")
