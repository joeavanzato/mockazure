"""Safe deleted-object recovery metadata and active-only user name uniqueness."""

from alembic import op

revision = "079"
down_revision = "078"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE TABLE identity_deleted_objects (
 tenant_id uuid NOT NULL REFERENCES tenants(id), object_id uuid NOT NULL,
 kind varchar(32) NOT NULL, deleted_at timestamptz NOT NULL,
 purged_at timestamptz, snapshot jsonb NOT NULL,
 PRIMARY KEY (tenant_id,object_id)
);
CREATE INDEX ix_identity_deleted_type ON identity_deleted_objects(tenant_id,kind,object_id);
ALTER TABLE identity_applications ADD COLUMN authority_valid_from timestamptz;
UPDATE identity_applications a SET authority_valid_from=now()
 WHERE a.deleted_at IS NOT NULL OR EXISTS (
 SELECT 1 FROM identity_service_principals s WHERE s.client_id=a.client_id
 AND s.deleted_at IS NOT NULL);
INSERT INTO identity_deleted_objects(tenant_id,object_id,kind,deleted_at,snapshot)
 SELECT tenant_id,id,'user',deleted_at,
 jsonb_build_object('legacy',true,'enabled',false,'owners','[]'::jsonb,
 'roles',coalesce((SELECT jsonb_agg(role) FROM identity_directory_roles r
 WHERE r.tenant_id=u.tenant_id AND r.user_id=u.id),'[]'::jsonb),
 'public',jsonb_build_object('id',id,'displayName',display_name,'userPrincipalName',upn,
 'accountEnabled',false,'userType',coalesce(profile->>'userType','Member')))
 FROM identity_users u WHERE deleted_at IS NOT NULL;
INSERT INTO identity_deleted_objects(tenant_id,object_id,kind,deleted_at,snapshot)
 SELECT tenant_id,object_id,'application',deleted_at,
 jsonb_build_object('legacy',true,'enabled',false,'owners','[]'::jsonb,
 'public',jsonb_build_object('id',object_id,'appId',client_id,'displayName',display_name))
 FROM identity_applications WHERE deleted_at IS NOT NULL;
INSERT INTO identity_deleted_objects(tenant_id,object_id,kind,deleted_at,snapshot)
 SELECT s.tenant_id,s.id,'servicePrincipal',s.deleted_at,
 jsonb_build_object('legacy',true,'enabled',false,'owners','[]'::jsonb,
 'public',jsonb_build_object('id',s.id,'appId',s.client_id,
 'displayName',a.display_name,'accountEnabled',false))
 FROM identity_service_principals s JOIN identity_applications a ON a.client_id=s.client_id
 WHERE s.deleted_at IS NOT NULL;
UPDATE oauth_sessions SET revoked_at=coalesce(revoked_at,now())
 WHERE user_id IN (SELECT id FROM identity_users WHERE deleted_at IS NOT NULL)
 OR client_id IN (SELECT client_id FROM identity_applications WHERE deleted_at IS NOT NULL)
 OR (tenant_id,client_id) IN (
 SELECT tenant_id,client_id FROM identity_service_principals WHERE deleted_at IS NOT NULL);
UPDATE identity_users SET sessions_valid_from=coalesce(sessions_valid_from,now())
 WHERE deleted_at IS NOT NULL;
UPDATE identity_credentials SET revoked_at=coalesce(revoked_at,now())
 WHERE client_id IN (SELECT client_id FROM identity_applications WHERE deleted_at IS NOT NULL)
 OR owner_id IN (SELECT id FROM identity_service_principals WHERE deleted_at IS NOT NULL);
DELETE FROM identity_grants g WHERE EXISTS (
 SELECT 1 FROM identity_service_principals s WHERE s.tenant_id=g.tenant_id
 AND s.client_id=g.client_id AND s.deleted_at IS NOT NULL)
 OR user_id IN (SELECT id FROM identity_users WHERE deleted_at IS NOT NULL);
DELETE FROM identity_directory_roles WHERE user_id IN (
 SELECT id FROM identity_users WHERE deleted_at IS NOT NULL);
DELETE FROM identity_service_principal_roles r WHERE EXISTS (
 SELECT 1 FROM identity_service_principals s WHERE s.client_id=r.client_id
 AND s.tenant_id=r.tenant_id AND s.deleted_at IS NOT NULL);
ALTER TABLE identity_users DROP CONSTRAINT identity_users_tenant_id_upn_key;
CREATE UNIQUE INDEX identity_users_live_upn ON identity_users(tenant_id,upn)
 WHERE deleted_at IS NULL;
""")


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS (SELECT 1 FROM identity_deleted_objects)
 THEN RAISE EXCEPTION 'Cannot discard deleted-object recovery state'; END IF;
 IF EXISTS (SELECT 1 FROM identity_applications WHERE authority_valid_from IS NOT NULL)
 THEN RAISE EXCEPTION 'Cannot discard application revocation state'; END IF;
 IF EXISTS (SELECT 1 FROM identity_users GROUP BY tenant_id,upn HAVING count(*)>1)
 THEN RAISE EXCEPTION 'Cannot discard reused identity names'; END IF;
END $$;
DROP INDEX identity_users_live_upn;
ALTER TABLE identity_users ADD CONSTRAINT identity_users_tenant_id_upn_key
 UNIQUE (tenant_id,upn);
DROP TABLE identity_deleted_objects;
ALTER TABLE identity_applications DROP COLUMN authority_valid_from;
""")
