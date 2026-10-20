"""Preserve native source availability independently of event time."""

from alembic import op

revision = "084"
down_revision = "083"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE hunting_records ADD COLUMN available_at timestamptz;
UPDATE hunting_records SET available_at = timestamp;
ALTER TABLE hunting_records ALTER COLUMN available_at SET NOT NULL;
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM hunting_records WHERE available_at <> timestamp)
 THEN RAISE EXCEPTION 'Cannot discard hunting source availability'; END IF;
END $$;
ALTER TABLE hunting_records DROP COLUMN available_at;
""")
