"""Durable pull-feed subscriptions, immutable publications, cursors and quotas."""

from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE activity_states (tenant_id uuid PRIMARY KEY REFERENCES tenants(id), generation
        uuid NOT NULL,
        revision bigint NOT NULL CHECK(revision>=0), settings jsonb NOT NULL);
    CREATE TABLE activity_subscriptions (tenant_id uuid REFERENCES tenants(id), client_id uuid,
        content_type varchar(40),
        enabled boolean NOT NULL, generation uuid NOT NULL, start_revision bigint NOT NULL
        CHECK(start_revision>=0),
        PRIMARY KEY(tenant_id,client_id,content_type));
    CREATE TABLE activity_contents (tenant_id uuid REFERENCES tenants(id), id varchar(64),
        generation uuid NOT NULL,
        record_id uuid NOT NULL, content_type varchar(40) NOT NULL, revision bigint NOT NULL
        CHECK(revision>0),
        published_at timestamptz NOT NULL, digest varchar(64) NOT NULL, records jsonb NOT NULL
        CHECK(jsonb_typeof(records)='array'),
        PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,generation,record_id));
    CREATE INDEX ix_activity_publication ON
        activity_contents(tenant_id,content_type,published_at,revision);
    CREATE TABLE activity_cursors (token_hash varchar(64) PRIMARY KEY, tenant_id uuid NOT NULL
        REFERENCES tenants(id),
        client_id uuid NOT NULL, expires_at timestamptz NOT NULL, state jsonb NOT NULL);
    CREATE INDEX ix_activity_cursors_tenant_id ON activity_cursors(tenant_id);
    CREATE INDEX ix_activity_cursors_expires_at ON activity_cursors(expires_at);
    CREATE TABLE activity_quotas (tenant_id uuid REFERENCES tenants(id),publisher_id
        uuid,starts_at timestamptz NOT NULL,
        requests integer NOT NULL CHECK(requests>=0),PRIMARY KEY(tenant_id,publisher_id));
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM activity_contents) OR EXISTS(SELECT 1 FROM activity_subscriptions)
      OR EXISTS(SELECT 1 FROM activity_states WHERE settings <> '{}'::jsonb) OR EXISTS(SELECT 1
        FROM jobs WHERE kind='activity.publish') THEN
      RAISE EXCEPTION 'Export and reset activity feed state and jobs before downgrade'; END IF;
        END $$;
      DROP TABLE
        activity_cursors,activity_quotas,activity_contents,activity_subscriptions,activity_states;
    """)
