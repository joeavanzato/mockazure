"""Provider state and internal service event publication."""

from alembic import op

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE cloud_state (
        tenant_id uuid PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
        generation bigint NOT NULL DEFAULT 1 CHECK(generation>0)
    );
    CREATE TABLE cloud_provider_registrations (
        tenant_id uuid NOT NULL, subscription_id uuid NOT NULL,
        namespace_key varchar(128) NOT NULL CHECK(namespace_key=lower(namespace_key)),
        state varchar(24) NOT NULL CHECK(state IN ('Registered','NotRegistered')),
        PRIMARY KEY(tenant_id,subscription_id,namespace_key),
        FOREIGN KEY(tenant_id,subscription_id)
            REFERENCES arm_subscriptions(tenant_id,id) ON DELETE CASCADE
    );
    CREATE TABLE cloud_service_events (
        tenant_id uuid NOT NULL REFERENCES cloud_state(tenant_id) ON DELETE CASCADE,
        id uuid NOT NULL, generation bigint NOT NULL, resource_id varchar(2048) NOT NULL,
        service varchar(64) NOT NULL, category varchar(128) NOT NULL,
        operation varchar(200) NOT NULL, occurred_at timestamptz NOT NULL,
        payload jsonb NOT NULL, publish_eligible boolean NOT NULL,
        PRIMARY KEY(tenant_id,id)
    );
    CREATE INDEX ix_cloud_event_publication ON cloud_service_events
        (tenant_id,generation,publish_eligible,occurred_at,id);
    """)


def downgrade():
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM cloud_provider_registrations)
         OR EXISTS(SELECT 1 FROM cloud_service_events)
      THEN RAISE EXCEPTION 'Cannot discard cloud provider or event state'; END IF;
    END $$;""")
    op.execute(
        "DROP TABLE cloud_service_events; DROP TABLE cloud_provider_registrations; "
        "DROP TABLE cloud_state;"
    )
