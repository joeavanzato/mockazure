"""Extend shared request budgets and one-shot faults to evidence families."""

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("graph_request_budgets", "graph_read_faults"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_resource_check")
        op.execute(f"""ALTER TABLE {table} ADD CONSTRAINT {table}_resource_check
            CHECK(resource IN ('users','groups','signIn','directoryAudit',
             'riskDetection','riskyUser'))""")


def downgrade():
    raise RuntimeError("Downgrade would discard evidence request budgets")
