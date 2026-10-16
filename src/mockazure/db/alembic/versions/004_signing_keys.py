"""Instance signing identity and durable client-assertion replay protection."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "identity_signing_keys",
        sa.Column("kid", sa.String(32), primary_key=True),
        sa.Column("encrypted_private", sa.Text(), nullable=False),
        sa.Column("public_jwk", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_identity_one_active_key",
        "identity_signing_keys",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )
    op.create_table(
        "identity_assertion_replays",
        sa.Column("tenant_id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.Uuid(), primary_key=True),
        sa.Column("jti", sa.String(200), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "client_id"],
            ["identity_service_principals.tenant_id", "identity_service_principals.client_id"],
        ),
    )
    op.create_index(
        "ix_identity_assertion_replays_expires_at", "identity_assertion_replays", ["expires_at"]
    )


def downgrade():
    op.drop_table("identity_assertion_replays")
    op.drop_table("identity_signing_keys")
