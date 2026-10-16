"""Provisioned ARM discovery inventory and explicit subscription Reader assignments."""

import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "arm_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.UniqueConstraint("tenant_id", "id"),
    )
    op.create_index("ix_arm_subscriptions_tenant_id", "arm_subscriptions", ["tenant_id"])
    op.create_table(
        "arm_reader_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid()),
        sa.Column("user_id", sa.Uuid()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "subscription_id"],
            ["arm_subscriptions.tenant_id", "arm_subscriptions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"], ["identity_users.tenant_id", "identity_users.id"]
        ),
        sa.CheckConstraint("(client_id IS NULL) <> (user_id IS NULL)"),
    )
    op.create_index("ix_arm_reader_assignments_tenant_id", "arm_reader_assignments", ["tenant_id"])
    op.create_index(
        "ix_arm_reader_assignments_subscription_id", "arm_reader_assignments", ["subscription_id"]
    )


def downgrade():
    op.drop_table("arm_reader_assignments")
    op.drop_table("arm_subscriptions")
