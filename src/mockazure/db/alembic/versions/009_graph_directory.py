"""Public directory state and commit-ordered history, preserving M2 identity."""

from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE identity_users ADD COLUMN profile jsonb NOT NULL DEFAULT '{}';
ALTER TABLE identity_users ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE identity_users ADD COLUMN deleted_at timestamptz;
ALTER TABLE identity_service_principals ADD CONSTRAINT uq_sp_tenant_id UNIQUE (tenant_id,id);

CREATE TABLE directory_state (
 tenant_id uuid PRIMARY KEY REFERENCES tenants(id),
 revision bigint NOT NULL CHECK(revision>=0), generation uuid NOT NULL);
CREATE TABLE directory_groups (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
 display_name varchar(200) NOT NULL, mail_nickname varchar(64) NOT NULL,
 profile jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
 deleted_at timestamptz, UNIQUE(tenant_id,id));
CREATE INDEX ix_directory_groups_tenant_id ON directory_groups(tenant_id);
CREATE TABLE directory_entries (
 tenant_id uuid NOT NULL REFERENCES tenants(id), id uuid NOT NULL, kind varchar(32) NOT NULL,
 user_id uuid, group_id uuid, sp_id uuid, PRIMARY KEY(tenant_id,id), UNIQUE(tenant_id,id,kind),
 FOREIGN KEY(tenant_id,user_id) REFERENCES identity_users(tenant_id,id),
 FOREIGN KEY(tenant_id,group_id) REFERENCES directory_groups(tenant_id,id),
 FOREIGN KEY(tenant_id,sp_id) REFERENCES identity_service_principals(tenant_id,id),
 CHECK (
  (kind='user' AND user_id=id AND user_id IS NOT NULL AND group_id IS NULL AND sp_id IS NULL) OR
  (kind='group' AND group_id=id AND group_id IS NOT NULL AND user_id IS NULL AND sp_id IS NULL) OR
  (kind='servicePrincipal' AND sp_id=id AND sp_id IS NOT NULL
  AND user_id IS NULL AND group_id IS NULL)));
CREATE TABLE directory_relations (
 tenant_id uuid NOT NULL, group_id uuid NOT NULL, object_id uuid NOT NULL,
 relation varchar(12) NOT NULL CHECK(relation IN ('member','owner')),
 PRIMARY KEY(tenant_id,group_id,object_id,relation),
 FOREIGN KEY(tenant_id,group_id) REFERENCES directory_groups(tenant_id,id),
 FOREIGN KEY(tenant_id,object_id) REFERENCES directory_entries(tenant_id,id));
CREATE INDEX ix_directory_relations_target ON directory_relations(tenant_id,object_id,relation);
CREATE TABLE directory_versions (
 tenant_id uuid NOT NULL, object_id uuid NOT NULL, valid_from bigint NOT NULL,
 valid_to bigint, kind varchar(32) NOT NULL, properties jsonb NOT NULL,
 changed_fields jsonb NOT NULL, removed boolean NOT NULL DEFAULT false,
 display_name varchar(200), upn varchar(320), created_at timestamptz,
 account_enabled boolean, mail_nickname varchar(64),
 PRIMARY KEY(tenant_id,object_id,valid_from),
 FOREIGN KEY(tenant_id,object_id,kind) REFERENCES directory_entries(tenant_id,id,kind),
 CHECK(valid_from>0 AND (valid_to IS NULL OR valid_to>valid_from)));
CREATE INDEX ix_directory_versions_round ON directory_versions(tenant_id,kind,valid_from,object_id);
CREATE INDEX ix_directory_versions_name
 ON directory_versions(tenant_id,kind,display_name,object_id);
CREATE UNIQUE INDEX uq_directory_versions_current ON directory_versions(tenant_id,object_id)
 WHERE valid_to IS NULL;
CREATE TABLE directory_relation_versions (
 tenant_id uuid NOT NULL, group_id uuid NOT NULL, object_id uuid NOT NULL,
 relation varchar(12) NOT NULL CHECK(relation IN ('member','owner')),
 valid_from bigint NOT NULL, valid_to bigint,
 PRIMARY KEY(tenant_id,group_id,object_id,relation,valid_from),
 FOREIGN KEY(tenant_id,group_id) REFERENCES directory_groups(tenant_id,id),
 FOREIGN KEY(tenant_id,object_id) REFERENCES directory_entries(tenant_id,id),
 CHECK(valid_from>0 AND (valid_to IS NULL OR valid_to>valid_from)));
CREATE INDEX ix_directory_relation_versions_target
 ON directory_relation_versions(tenant_id,object_id,valid_from);
CREATE UNIQUE INDEX uq_directory_relation_versions_current
 ON directory_relation_versions(tenant_id,group_id,object_id,relation) WHERE valid_to IS NULL;
CREATE TABLE directory_evidence_outbox (
 id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id), revision bigint NOT NULL,
 object_id uuid NOT NULL, kind varchar(32) NOT NULL, facts jsonb NOT NULL,
 occurred_at timestamptz NOT NULL DEFAULT now(), projected_at timestamptz,
 UNIQUE(tenant_id,revision));
CREATE INDEX ix_directory_evidence_outbox_tenant_id ON directory_evidence_outbox(tenant_id);
CREATE INDEX ix_directory_outbox_pending ON directory_evidence_outbox(tenant_id,occurred_at)
 WHERE projected_at IS NULL;
CREATE TABLE directory_continuations (
 token_hash varchar(64) PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
 state jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL);
CREATE INDEX ix_directory_continuations_expiry ON directory_continuations(tenant_id,expires_at);

INSERT INTO directory_state(tenant_id,revision,generation)
 SELECT id,1,gen_random_uuid() FROM tenants;
INSERT INTO directory_entries(tenant_id,id,kind,user_id)
 SELECT tenant_id,id,'user',id FROM identity_users;
INSERT INTO directory_entries(tenant_id,id,kind,sp_id)
 SELECT tenant_id,id,'servicePrincipal',id FROM identity_service_principals;
INSERT INTO directory_versions(tenant_id,object_id,valid_from,kind,properties,changed_fields,
 display_name,upn,created_at,account_enabled,mail_nickname)
 SELECT tenant_id,id,1,'user',
 jsonb_build_object('id',id,'displayName',display_name,'userPrincipalName',upn,
 'accountEnabled',enabled,'createdDateTime',to_char(created_at AT TIME ZONE 'UTC',
 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),'userType','Member','mailNickname',left(split_part(upn,'@',1),64),
 'givenName',NULL,'surname',NULL,'mail',NULL,'jobTitle',NULL,'department',NULL,
 'officeLocation',NULL,'mobilePhone',NULL,'businessPhones','[]'::jsonb,'preferredLanguage',NULL),
 '["id","displayName","userPrincipalName","accountEnabled"]'::jsonb,
 display_name,upn,created_at,enabled,left(split_part(upn,'@',1),64) FROM identity_users;
""")


def downgrade():
    raise RuntimeError("Directory history downgrade is destructive; restore an operational backup")
