"""Persistent calendar state, event history and durable meeting jobs."""

from alembic import op

revision = "069"
down_revision = "068"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE calendar_state (
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,generation uuid NOT NULL,
 revision bigint NOT NULL,default_calendar_id varchar(128) NOT NULL,
 default_group_id varchar(128) NOT NULL,PRIMARY KEY(tenant_id,user_id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES mail_mailboxes(tenant_id,user_id) ON DELETE CASCADE);
CREATE TABLE calendar_groups (
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,id varchar(128) NOT NULL,payload jsonb NOT NULL,
 PRIMARY KEY(tenant_id,user_id,id),FOREIGN KEY(tenant_id,user_id)
 REFERENCES calendar_state(tenant_id,user_id) ON DELETE CASCADE);
CREATE TABLE calendars (
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,id varchar(128) NOT NULL,
 group_id varchar(128) NOT NULL,payload jsonb NOT NULL,PRIMARY KEY(tenant_id,user_id,id),
 FOREIGN KEY(tenant_id,user_id,group_id)
 REFERENCES calendar_groups(tenant_id,user_id,id) ON DELETE CASCADE);
CREATE TABLE calendar_events (
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,id varchar(128) NOT NULL,
 calendar_id varchar(128) NOT NULL,meeting_id uuid NOT NULL,organizer_id uuid NOT NULL,
 revision bigint NOT NULL,source_revision bigint NOT NULL,deleted boolean NOT NULL,
 payload jsonb NOT NULL,transaction_client uuid,transaction_id varchar(256),
 PRIMARY KEY(tenant_id,user_id,id),FOREIGN KEY(tenant_id,user_id,calendar_id)
 REFERENCES calendars(tenant_id,user_id,id) ON DELETE CASCADE);
CREATE INDEX calendar_events_meeting ON calendar_events(tenant_id,meeting_id,user_id);
CREATE UNIQUE INDEX calendar_event_transaction ON calendar_events
 (tenant_id,user_id,calendar_id,transaction_client,transaction_id) WHERE transaction_id IS NOT NULL;
CREATE TABLE calendar_event_versions (
 tenant_id uuid NOT NULL,user_id uuid NOT NULL,id varchar(128) NOT NULL,
 revision bigint NOT NULL,calendar_id varchar(128) NOT NULL,deleted boolean NOT NULL,
 payload jsonb NOT NULL,occurred_at timestamptz NOT NULL,PRIMARY KEY(tenant_id,user_id,id,revision),
 FOREIGN KEY(tenant_id,user_id) REFERENCES calendar_state(tenant_id,user_id) ON DELETE CASCADE);
CREATE INDEX calendar_event_changes ON calendar_event_versions(tenant_id,user_id,revision);
CREATE TABLE calendar_meeting_deliveries (
 id uuid PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,tenant_id uuid NOT NULL,
 user_id uuid NOT NULL,generation uuid NOT NULL,evidence_generation uuid NOT NULL,
 cloud_generation bigint NOT NULL,actor jsonb NOT NULL,operation varchar(32) NOT NULL,
 event_id varchar(128) NOT NULL,snapshot jsonb NOT NULL,FOREIGN KEY(tenant_id,user_id)
 REFERENCES calendar_state(tenant_id,user_id) ON DELETE CASCADE);
""")


def downgrade():
    op.execute("""DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM calendar_state)
 THEN RAISE EXCEPTION 'Reset calendar state before downgrade'; END IF; END $$;
 DROP TABLE calendar_meeting_deliveries;
 DROP TABLE calendar_event_versions;
 DROP TABLE calendar_events;
 DROP TABLE calendars;
 DROP TABLE calendar_groups;
 DROP TABLE calendar_state;
""")
