"""Persistent Graph security objects and immutable collection pages."""

from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE identity_applications ADD COLUMN object_id uuid NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE identity_applications ADD CONSTRAINT identity_applications_object_id_key
 UNIQUE(object_id);
ALTER TABLE identity_applications ADD COLUMN profile jsonb NOT NULL DEFAULT '{}';
ALTER TABLE identity_service_principals ADD COLUMN profile jsonb NOT NULL DEFAULT '{}';
CREATE TABLE security_objects(
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 id uuid NOT NULL DEFAULT gen_random_uuid(),
 kind varchar(40) NOT NULL CHECK(kind IN
 ('device','resourceServicePrincipal','conditionalAccessPolicy','ipNamedLocation',
  'authenticationMethod')),
 properties jsonb NOT NULL DEFAULT '{}',
 PRIMARY KEY(tenant_id,id)
);
CREATE INDEX ix_security_objects_kind ON security_objects(tenant_id,kind);
CREATE TABLE security_owners(
 tenant_id uuid NOT NULL REFERENCES tenants(id), object_id uuid NOT NULL, owner_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,object_id,owner_id)
);
CREATE TABLE security_snapshots(
 tenant_id uuid NOT NULL REFERENCES tenants(id), id uuid NOT NULL,
 binding jsonb NOT NULL, query jsonb NOT NULL, signing_secret varchar(64) NOT NULL,
 expires_at timestamptz NOT NULL, PRIMARY KEY(tenant_id,id)
);
CREATE INDEX ix_security_snapshots_expires_at ON security_snapshots(expires_at);
CREATE TABLE security_snapshot_items(
 tenant_id uuid NOT NULL, snapshot_id uuid NOT NULL, position bigint NOT NULL,
 payload jsonb NOT NULL, PRIMARY KEY(tenant_id,snapshot_id,position),
 FOREIGN KEY(tenant_id,snapshot_id) REFERENCES security_snapshots(tenant_id,id) ON DELETE CASCADE
);
""")


def downgrade():
    op.execute("""
DROP TABLE security_snapshot_items;
DROP TABLE security_snapshots;
DROP TABLE security_owners;
DROP TABLE security_objects;
ALTER TABLE identity_service_principals DROP COLUMN profile;
ALTER TABLE identity_applications DROP COLUMN profile;
ALTER TABLE identity_applications DROP COLUMN object_id;
""")
