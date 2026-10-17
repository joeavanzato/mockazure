"""Separate device registered users from registered owners."""

from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE security_objects DROP CONSTRAINT security_objects_kind_check;
ALTER TABLE security_objects ADD CONSTRAINT security_objects_kind_check CHECK(kind IN
 ('device','resourceServicePrincipal','conditionalAccessPolicy','ipNamedLocation',
  'authenticationMethod','organizationProfile','principalAppRoleAssignment'));
CREATE INDEX security_principal_assignment_idx ON security_objects(tenant_id,
 (properties->>'principalId'), (properties->>'resourceId'))
 WHERE kind='principalAppRoleAssignment';
CREATE TABLE security_device_users (
 tenant_id UUID NOT NULL, device_id UUID NOT NULL, user_id UUID NOT NULL,
 PRIMARY KEY (tenant_id, device_id, user_id),
 FOREIGN KEY (tenant_id, device_id) REFERENCES security_objects(tenant_id,id) ON DELETE CASCADE,
 FOREIGN KEY (tenant_id, user_id) REFERENCES identity_users(tenant_id,id) ON DELETE CASCADE
);
CREATE INDEX security_device_users_user_idx ON security_device_users(tenant_id,user_id);
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM security_objects WHERE kind IN
 ('organizationProfile','principalAppRoleAssignment'))
 OR EXISTS (SELECT 1 FROM security_device_users) THEN
 RAISE EXCEPTION 'Export and remove collection state before downgrading';
 END IF;
END $$;
DROP INDEX security_principal_assignment_idx;
ALTER TABLE security_objects DROP CONSTRAINT security_objects_kind_check;
ALTER TABLE security_objects ADD CONSTRAINT security_objects_kind_check CHECK(kind IN
 ('device','resourceServicePrincipal','conditionalAccessPolicy','ipNamedLocation','authenticationMethod'));
""")
    op.drop_table("security_device_users")
