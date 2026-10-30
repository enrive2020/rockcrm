/**
 * Auth & tenancy shared types (module: auth-tenancy). See docs/product/api-contract.md §2 and prd.md §3.3.
 * Roles mirror the DDL CHECK on membership.role.
 */
export const ROLES = ['owner', 'admin', 'manager', 'waiter', 'cook', 'accountant', 'partner_integrator'] as const;
export type Role = (typeof ROLES)[number];

export const SCOPES = [
  'org:read', 'org:write', 'org:billing',
  'users:read', 'users:write', 'staff:write',
  'venues:read', 'venues:create', 'venues:write',
  'menu:read', 'menu:write', 'stoplist:write',
  'orders:read', 'orders:write', 'calls:write',
  'bookings:read', 'bookings:write',
  'reviews:read', 'reviews:write',
  'guests:read', 'guests:export',
  'analytics:read', 'audit:read', 'ai:config',
  'invoices:read', 'invoices:write', 'shifts:read', 'shifts:write', 'tips:read',
] as const;
export type Scope = (typeof SCOPES)[number];

const ALL: readonly Scope[] = SCOPES;
/** Role → scopes. Venue-bound roles get these scopes only for their venues (see RoleGrant.v). */
export const ROLE_SCOPES: Record<Role, readonly Scope[]> = {
  owner: ALL,
  admin: ALL.filter((s) => s !== 'org:billing' && s !== 'guests:export'),
  manager: ['org:read', 'users:read', 'staff:write', 'venues:read', 'venues:write', 'menu:read', 'menu:write',
    'stoplist:write', 'orders:read', 'orders:write', 'calls:write', 'bookings:read', 'bookings:write',
    'reviews:read', 'reviews:write', 'guests:read', 'analytics:read', 'audit:read', 'ai:config',
    'invoices:read', 'invoices:write', 'shifts:read', 'shifts:write', 'tips:read'],
  waiter: ['venues:read', 'menu:read', 'orders:read', 'orders:write', 'calls:write', 'bookings:read', 'shifts:read', 'tips:read'],
  cook: ['venues:read', 'menu:read', 'orders:read', 'stoplist:write', 'shifts:read'],
  accountant: ['org:read', 'venues:read', 'analytics:read', 'invoices:read', 'invoices:write', 'tips:read', 'audit:read'],
  partner_integrator: ['venues:read', 'menu:read', 'menu:write', 'analytics:read'],
};

/** Roles that a `manager` (staff:write) may assign within their own venues. */
export const STAFF_ROLES: readonly Role[] = ['waiter', 'cook'];

/** A role grant inside one organization: `v` = '*' (all venues) or a list of venue ids. */
export interface RoleGrant { r: Role; v: '*' | string[] }

/** Access JWT claims (aud=admin). */
export interface AccessClaims {
  iss: string; sub: string; aud: 'admin'; org: string;
  roles: RoleGrant[]; scp: Scope[];
  iat: number; exp: number; jti: string;
}

/** Guest JWT claims (aud=guest), no PII. */
export interface GuestClaims { iss: string; sub: string; aud: 'guest'; org: string; ven: string; tbl?: string; iat: number; exp: number; jti: string }

/** Pseudo-tenant for platform-wide tables (app_user, refresh tokens, OTP, slug registry). */
export const PLATFORM_ORG_ID = '00000000-0000-0000-0000-000000000000';

export interface TokenPair { access_token: string; refresh_token: string; token_type: 'Bearer'; expires_in: number }
