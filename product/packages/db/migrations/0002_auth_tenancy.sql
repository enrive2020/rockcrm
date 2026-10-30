-- Module auth-tenancy. Forward-only.
-- The Repo layer always writes organization_id; platform-wide tables use the pseudo-tenant
-- 00000000-0000-0000-0000-000000000000 (PLATFORM_ORG_ID) and RLS allows it only to the auth service role.

ALTER TABLE organization ADD COLUMN IF NOT EXISTS organization_id uuid;
UPDATE organization SET organization_id = id WHERE organization_id IS NULL;
ALTER TABLE organization ALTER COLUMN organization_id SET NOT NULL;
ALTER TABLE organization ADD CONSTRAINT ck_org_self CHECK (organization_id = id);

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS organization_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000';
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS disabled_at timestamptz;

ALTER TABLE membership ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE membership ADD COLUMN IF NOT EXISTS revoked_at timestamptz;

-- user -> organizations index (platform), to resolve orgs at login without crossing tenants
CREATE TABLE user_org (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
  user_id uuid NOT NULL REFERENCES app_user(id),
  org_id uuid NOT NULL REFERENCES organization(id),
  revoked_at timestamptz,
  UNIQUE (user_id, org_id)
);

CREATE TABLE refresh_token (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
  user_id uuid NOT NULL REFERENCES app_user(id),
  org_id uuid NOT NULL REFERENCES organization(id),
  family_id uuid NOT NULL,
  token_hash char(64) NOT NULL,
  expires_at timestamptz NOT NULL,
  rotated_at timestamptz, revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_refresh_family ON refresh_token(family_id);

CREATE TABLE auth_otp (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
  phone varchar(16) NOT NULL,
  code_hash char(64) NOT NULL,
  attempts int NOT NULL DEFAULT 0,
  expires_at timestamptz NOT NULL, consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_otp_phone ON auth_otp(phone, created_at);

CREATE TABLE venue_slug (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
  slug citext NOT NULL UNIQUE,
  venue_id uuid NOT NULL
);

CREATE TABLE user_invitation (
  id uuid PRIMARY KEY,
  organization_id uuid NOT NULL REFERENCES organization(id),
  email citext, phone varchar(16),
  role text NOT NULL, venue_ids uuid[],
  token_hash char(64) NOT NULL,
  invited_by uuid NOT NULL REFERENCES app_user(id),
  expires_at timestamptz NOT NULL, accepted_at timestamptz, revoked_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (email IS NOT NULL OR phone IS NOT NULL)
);

ALTER TABLE user_invitation ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_invitation FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_invitation
  USING (organization_id = current_setting('app.org_id', true)::uuid);
