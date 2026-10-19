"""Durable, locally bound Graph subscriptions and callback outbox."""

from alembic import op

revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE graph_subscriptions (
id uuid PRIMARY KEY,
tenant_id uuid NOT NULL REFERENCES tenants(id),
client_id uuid NOT NULL,subject_id uuid,
actor jsonb NOT NULL,target jsonb NOT NULL,receiver_bindings jsonb NOT NULL,payload jsonb NOT NULL,
revision bigint NOT NULL,deleted boolean NOT NULL,expires_at timestamptz NOT NULL,
created_at timestamptz NOT NULL,updated_at timestamptz NOT NULL,UNIQUE(tenant_id,id));
CREATE INDEX ix_graph_subscriptions_tenant_id ON graph_subscriptions(tenant_id);
CREATE TABLE graph_notifications (
id uuid PRIMARY KEY,
tenant_id uuid NOT NULL REFERENCES tenants(id),
subscription_id uuid NOT NULL,
subscription_revision bigint NOT NULL,source_key varchar(256) NOT NULL,
change_type varchar(40) NOT NULL,
payload jsonb NOT NULL,
attempts integer NOT NULL,deadline timestamptz NOT NULL,
UNIQUE(subscription_id,source_key,change_type),
FOREIGN KEY(tenant_id,subscription_id)
REFERENCES graph_subscriptions(tenant_id,id) ON DELETE CASCADE,
FOREIGN KEY(tenant_id,id) REFERENCES jobs(tenant_id,id) ON DELETE CASCADE);
CREATE INDEX ix_graph_notifications_tenant_id ON graph_notifications(tenant_id);
CREATE INDEX ix_graph_notifications_subscription_id ON graph_notifications(subscription_id);""")


def downgrade():
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM graph_subscriptions)
OR EXISTS(SELECT 1 FROM graph_notifications)
THEN RAISE EXCEPTION 'Reset subscriptions before downgrade'; END IF; END $$;
DROP TABLE graph_notifications;DROP TABLE graph_subscriptions;""")
