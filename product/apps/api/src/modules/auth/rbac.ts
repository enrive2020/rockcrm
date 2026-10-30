import { ROLE_SCOPES, STAFF_ROLES, type Role, type RoleGrant, type Scope } from '@podacha/shared';

/** Org-wide scopes: from grants with v='*'. */
export function orgScopes(roles: RoleGrant[]): Scope[] {
  const s = new Set<Scope>();
  for (const g of roles) if (g.v === '*') ROLE_SCOPES[g.r].forEach((x) => s.add(x));
  return [...s];
}
/** All scopes the user has anywhere (for the `scp` claim / UI). */
export function allScopes(roles: RoleGrant[]): Scope[] {
  const s = new Set<Scope>(); for (const g of roles) ROLE_SCOPES[g.r].forEach((x) => s.add(x)); return [...s].sort();
}
export function canAccessVenue(roles: RoleGrant[], venueId: string): boolean {
  return roles.some((g) => g.v === '*' || g.v.includes(venueId));
}
/** Scope check; with venueId, only grants that cover the venue count; without — only org-wide grants. */
export function hasScope(roles: RoleGrant[], scope: Scope, venueId?: string): boolean {
  return roles.some((g) => (venueId ? g.v === '*' || g.v.includes(venueId) : g.v === '*') && ROLE_SCOPES[g.r].includes(scope));
}
/** Venues the user is limited to, or '*' for all. */
export function venueScope(roles: RoleGrant[]): '*' | string[] {
  if (roles.some((g) => g.v === '*')) return '*';
  return [...new Set(roles.flatMap((g) => (g.v === '*' ? [] : g.v)))];
}

export function grantOf(role: Role, venueIds: string[] | null | undefined): RoleGrant {
  return { r: role, v: role === 'owner' || !venueIds || venueIds.length === 0 ? '*' : [...venueIds] };
}

/**
 * Can `actor` assign `target` role with `venueIds` (null = all venues)?
 * owner: anything; admin: anything but owner; manager: waiter/cook within own venues.
 */
export function canAssign(actor: RoleGrant[], target: Role, venueIds: string[] | null): boolean {
  if (actor.some((g) => g.r === 'owner')) return true;
  if (hasScope(actor, 'users:write')) return target !== 'owner';
  if (!STAFF_ROLES.includes(target) || !venueIds || venueIds.length === 0) return false;
  return venueIds.every((v) => hasScope(actor, 'staff:write', v));
}
