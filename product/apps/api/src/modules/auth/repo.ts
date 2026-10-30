import type { Db, TenantContext } from '@podacha/db';
import { PLATFORM_ORG_ID, type Role } from '@podacha/shared';

/** Row shapes owned by the auth module (snake_case as in DB). */
export type UserRow = { id: string; organization_id?: string; phone: string | null; email: string | null; full_name: string | null;
  password_hash: string | null; telegram_id: number | null; last_login_at: string | null; created_at: string; disabled_at?: string | null };
export type MembershipRow = { id: string; organization_id?: string; user_id: string; role: Role; venue_ids: string[] | null;
  created_at?: string; revoked_at?: string | null };
export type UserOrgRow = { id: string; organization_id?: string; user_id: string; org_id: string; revoked_at: string | null };
export type RefreshRow = { id: string; organization_id?: string; user_id: string; org_id: string; family_id: string; token_hash: string;
  expires_at: string; rotated_at: string | null; revoked_at: string | null; created_at: string };
export type OtpRow = { id: string; organization_id?: string; phone: string; code_hash: string; attempts: number; expires_at: string;
  consumed_at: string | null; created_at: string };
export type InvitationRow = { id: string; organization_id?: string; email: string | null; phone: string | null; role: Role;
  venue_ids: string[] | null; token_hash: string; invited_by: string; expires_at: string; accepted_at: string | null;
  revoked_at: string | null; created_at: string };

export const PLATFORM: TenantContext = { organizationId: PLATFORM_ORG_ID };

export function authRepos(db: Db) {
  return {
    users: db.repo<UserRow>('app_user'),
    userOrgs: db.repo<UserOrgRow>('user_org'),
    refresh: db.repo<RefreshRow>('refresh_token'),
    otp: db.repo<OtpRow>('auth_otp'),
    memberships: db.repo<MembershipRow>('membership'),
    invitations: db.repo<InvitationRow>('user_invitation'),
  };
}
export type AuthRepos = ReturnType<typeof authRepos>;
