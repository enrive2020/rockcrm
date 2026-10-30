import type { Db, TenantContext } from '@podacha/db';
import { AppError, STAFF_ROLES, uuidv7, type Role, type RoleGrant, type TokenPair } from '@podacha/shared';
import type { z } from 'zod';
import { authConfig } from './config.js';
import { hashPassword, otpCode, randomToken, safeEqualHex, sha256, verifyPassword } from './crypto.js';
import { signJwt } from './jwt.js';
import { allScopes, canAssign, grantOf, hasScope } from './rbac.js';
import { authRepos, PLATFORM, type InvitationRow, type MembershipRow, type UserRow } from './repo.js';
import type { acceptInviteBody, inviteBody, signupBody, updateMemberBody } from './schemas.js';
import type { AuthContext } from './guard.js';
import { assertVenuesExist, createOrganization, getOrganization } from '../tenancy/service.js';

const iso = (ms: number) => new Date(ms).toISOString();
const now = () => authConfig().now();
const unauthorized = (m = 'Invalid credentials') => new AppError('UNAUTHORIZED', 401, m);
const orgCtx = (organizationId: string): TenantContext => ({ organizationId });

export interface AuthResult extends TokenPair { user: ReturnType<typeof userDto>; organization_id: string }

export const userDto = (u: UserRow) => ({ id: u.id, email: u.email, phone: u.phone, full_name: u.full_name });

// ---------- users ----------
async function findUserByLogin(db: Db, login: string): Promise<UserRow | null> {
  const r = authRepos(db);
  const l = login.trim();
  if (l.includes('@')) return (await r.users.list(PLATFORM, { email: l.toLowerCase() }, 1))[0] ?? null;
  const digits = l.replace(/\D/g, '');
  const m = /^(?:7|8)?(\d{10})$/.exec(digits);
  return m ? ((await r.users.list(PLATFORM, { phone: `+7${m[1]}` }, 1))[0] ?? null) : null;
}

async function createUser(db: Db, u: { email?: string | null; phone?: string | null; full_name?: string | null; password?: string }) {
  const r = authRepos(db);
  if (u.email && (await r.users.list(PLATFORM, { email: u.email }, 1)).length) throw new AppError('CONFLICT', 409, 'Email already registered');
  if (u.phone && (await r.users.list(PLATFORM, { phone: u.phone }, 1)).length) throw new AppError('CONFLICT', 409, 'Phone already registered');
  return r.users.insert(PLATFORM, { id: uuidv7(), email: u.email ?? null, phone: u.phone ?? null, full_name: u.full_name ?? null,
    password_hash: u.password ? await hashPassword(u.password) : null, telegram_id: null, last_login_at: null,
    created_at: iso(now()), disabled_at: null });
}

// ---------- memberships ----------
async function activeMembership(db: Db, orgId: string, userId: string): Promise<MembershipRow | null> {
  const list = await authRepos(db).memberships.list(orgCtx(orgId), { user_id: userId }, 5);
  return list.find((m) => !m.revoked_at) ?? null;
}

async function addMembership(db: Db, orgId: string, userId: string, role: Role, venueIds: string[] | null) {
  const r = authRepos(db);
  const existing = (await r.memberships.list(orgCtx(orgId), { user_id: userId }, 5))[0];
  const m = existing
    ? (await r.memberships.update(orgCtx(orgId), existing.id, { role, venue_ids: venueIds, revoked_at: null }))!
    : await r.memberships.insert(orgCtx(orgId), { id: uuidv7(), user_id: userId, role, venue_ids: venueIds, created_at: iso(now()), revoked_at: null });
  const link = (await r.userOrgs.list(PLATFORM, { user_id: userId, org_id: orgId }, 1))[0];
  if (link) await r.userOrgs.update(PLATFORM, link.id, { revoked_at: null });
  else await r.userOrgs.insert(PLATFORM, { id: uuidv7(), user_id: userId, org_id: orgId, revoked_at: null });
  return m;
}

async function userOrgIds(db: Db, userId: string): Promise<string[]> {
  return (await authRepos(db).userOrgs.list(PLATFORM, { user_id: userId }, 200)).filter((l) => !l.revoked_at).map((l) => l.org_id);
}

// ---------- tokens ----------
async function issueTokens(db: Db, user: UserRow, orgId: string, familyId?: string): Promise<TokenPair> {
  if (user.disabled_at) throw unauthorized('User disabled');
  const m = await activeMembership(db, orgId, user.id);
  if (!m) throw unauthorized('No access to organization');
  const c = authConfig();
  const roles: RoleGrant[] = [grantOf(m.role, m.venue_ids)];
  const iat = Math.floor(now() / 1000);
  const access = signJwt(c.keys, { iss: c.issuer, sub: user.id, aud: 'admin', org: orgId, roles, scp: allScopes(roles), iat, exp: iat + c.accessTtlSec });
  const id = uuidv7(); const secret = randomToken();
  await authRepos(db).refresh.insert(PLATFORM, { id, user_id: user.id, org_id: orgId, family_id: familyId ?? id,
    token_hash: sha256(secret), expires_at: iso(now() + c.refreshTtlSec * 1000), rotated_at: null, revoked_at: null, created_at: iso(now()) });
  return { access_token: access, refresh_token: `${id}.${secret}`, token_type: 'Bearer', expires_in: c.accessTtlSec };
}

async function pickOrg(db: Db, userId: string, wanted?: string): Promise<string> {
  const orgs = await userOrgIds(db, userId);
  if (wanted) { if (!orgs.includes(wanted)) throw unauthorized('No access to organization'); return wanted; }
  if (!orgs[0]) throw unauthorized('No active organization');
  return orgs[0];
}

async function finishLogin(db: Db, user: UserRow, wantedOrg?: string): Promise<AuthResult> {
  const orgId = await pickOrg(db, user.id, wantedOrg);
  const tokens = await issueTokens(db, user, orgId);
  await authRepos(db).users.update(PLATFORM, user.id, { last_login_at: iso(now()) });
  return { ...tokens, user: userDto(user), organization_id: orgId };
}

async function revokeFamily(db: Db, familyId: string) {
  const r = authRepos(db).refresh;
  for (const t of await r.list(PLATFORM, { family_id: familyId }, 200))
    if (!t.revoked_at) await r.update(PLATFORM, t.id, { revoked_at: iso(now()) });
}

// ---------- public API ----------
export async function signup(db: Db, b: z.output<typeof signupBody>): Promise<AuthResult & { organization: { id: string; legal_name: string } }> {
  const user = await createUser(db, { email: b.email, phone: b.phone, full_name: b.full_name, password: b.password });
  const org = await createOrganization(db, b.organization);
  await addMembership(db, org.id, user.id, 'owner', null);
  const res = await finishLogin(db, user, org.id);
  return { ...res, organization: { id: org.id, legal_name: org.legal_name } };
}

export async function login(db: Db, loginId: string, password: string, orgId?: string): Promise<AuthResult> {
  const user = await findUserByLogin(db, loginId);
  const ok = await verifyPassword(password, user?.password_hash);
  if (!user || !ok) throw unauthorized();
  return finishLogin(db, user, orgId);
}

export async function requestOtp(db: Db, phone: string): Promise<{ expires_in: number }> {
  const c = authConfig(); const r = authRepos(db);
  const recent = (await r.otp.list(PLATFORM, { phone }, 200)).filter((o) => Date.parse(o.created_at) > now() - c.otpWindowSec * 1000);
  if (recent.length >= c.otpMaxPerWindow) throw new AppError('RATE_LIMITED', 429, 'Too many OTP requests');
  for (const o of recent) if (!o.consumed_at) await r.otp.update(PLATFORM, o.id, { consumed_at: iso(now()) });
  const code = otpCode();
  await r.otp.insert(PLATFORM, { id: uuidv7(), phone, code_hash: sha256(`${phone}:${code}`), attempts: 0,
    expires_at: iso(now() + c.otpTtlSec * 1000), consumed_at: null, created_at: iso(now()) });
  // Do not reveal whether the phone is registered: always "send". Unknown phones simply never succeed at verify.
  const user = (await r.users.list(PLATFORM, { phone }, 1))[0];
  if (user) await c.otpSender.send(phone, code);
  return { expires_in: c.otpTtlSec };
}

export async function verifyOtp(db: Db, phone: string, code: string, orgId?: string): Promise<AuthResult> {
  const c = authConfig(); const r = authRepos(db);
  const live = (await r.otp.list(PLATFORM, { phone }, 200))
    .filter((o) => !o.consumed_at && Date.parse(o.expires_at) > now()).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  if (!live) throw unauthorized('Code expired or not requested');
  if (!safeEqualHex(live.code_hash, sha256(`${phone}:${code}`))) {
    const attempts = live.attempts + 1;
    await r.otp.update(PLATFORM, live.id, { attempts, ...(attempts >= c.otpMaxAttempts ? { consumed_at: iso(now()) } : {}) });
    throw unauthorized('Invalid code');
  }
  await r.otp.update(PLATFORM, live.id, { consumed_at: iso(now()) });
  const user = (await r.users.list(PLATFORM, { phone }, 1))[0];
  if (!user) throw unauthorized('Invalid code');
  return finishLogin(db, user, orgId);
}

export async function refresh(db: Db, token: string): Promise<TokenPair> {
  const [id, secret] = token.split('.');
  if (!id || !secret || !/^[0-9a-f-]{36}$/.test(id)) throw unauthorized('Invalid refresh token');
  const r = authRepos(db);
  const row = await r.refresh.get(PLATFORM, id);
  if (!row || !safeEqualHex(row.token_hash, sha256(secret))) throw unauthorized('Invalid refresh token');
  if (row.revoked_at) throw unauthorized('Refresh token revoked');
  if (row.rotated_at) { await revokeFamily(db, row.family_id); throw unauthorized('Refresh token reuse detected'); }
  if (Date.parse(row.expires_at) <= now()) throw unauthorized('Refresh token expired');
  await r.refresh.update(PLATFORM, id, { rotated_at: iso(now()) });
  const user = await r.users.get(PLATFORM, row.user_id);
  if (!user) throw unauthorized();
  try { return await issueTokens(db, user, row.org_id, row.family_id); }
  catch (e) { await revokeFamily(db, row.family_id); throw e; }
}

export async function logout(db: Db, token: string): Promise<void> {
  const [id, secret] = token.split('.');
  if (!id || !secret || !/^[0-9a-f-]{36}$/.test(id)) return;
  const row = await authRepos(db).refresh.get(PLATFORM, id);
  if (row && safeEqualHex(row.token_hash, sha256(secret))) await revokeFamily(db, row.family_id);
}

export async function switchOrg(db: Db, auth: AuthContext, orgId: string): Promise<AuthResult> {
  const user = await authRepos(db).users.get(PLATFORM, auth.userId);
  if (!user) throw unauthorized();
  return finishLogin(db, user, orgId);
}

export async function changePassword(db: Db, auth: AuthContext, current: string | undefined, next: string) {
  const r = authRepos(db);
  const user = await r.users.get(PLATFORM, auth.userId);
  if (!user) throw unauthorized();
  if (user.password_hash && !(await verifyPassword(current ?? '', user.password_hash))) throw unauthorized('Wrong current password');
  await r.users.update(PLATFORM, user.id, { password_hash: await hashPassword(next) });
}

export async function me(db: Db, auth: AuthContext) {
  const user = await authRepos(db).users.get(PLATFORM, auth.userId);
  if (!user) throw new AppError('NOT_FOUND', 404, 'User not found');
  const orgs = [];
  for (const id of await userOrgIds(db, user.id)) {
    const m = await activeMembership(db, id, user.id);
    if (!m) continue;
    const o = await getOrganization(db, orgCtx(id)).catch(() => null);
    orgs.push({ id, legal_name: o?.legal_name ?? null, role: m.role, venue_ids: m.venue_ids });
  }
  return { user: userDto(user), organization_id: auth.organizationId, roles: auth.roles, scopes: auth.scopes, organizations: orgs };
}

// ---------- members (users of an organization) ----------
const memberDto = (m: MembershipRow, u: UserRow | null) => ({ id: m.user_id, membership_id: m.id, role: m.role, venue_ids: m.venue_ids,
  full_name: u?.full_name ?? null, email: u?.email ?? null, phone: u?.phone ?? null, created_at: m.created_at ?? null });

/** Managers only see members whose venues intersect theirs (and never see org-wide roles' details beyond the list). */
function visibleTo(auth: AuthContext, m: MembershipRow): boolean {
  if (hasScope(auth.roles, 'users:read')) return true;
  if (m.user_id === auth.userId) return true;
  return STAFF_ROLES.includes(m.role) && !!m.venue_ids?.length && m.venue_ids.some((v) => hasScope(auth.roles, 'staff:write', v));
}

export async function listMembers(db: Db, auth: AuthContext) {
  const r = authRepos(db);
  const ms = (await r.memberships.list(orgCtx(auth.organizationId), {}, 200)).filter((m) => !m.revoked_at && visibleTo(auth, m));
  const out = [];
  for (const m of ms) out.push(memberDto(m, await r.users.get(PLATFORM, m.user_id)));
  return out;
}

async function getMemberRow(db: Db, auth: AuthContext, userId: string): Promise<MembershipRow> {
  const m = await activeMembership(db, auth.organizationId, userId);
  if (!m || !visibleTo(auth, m)) throw new AppError('NOT_FOUND', 404, 'User not found');
  return m;
}

async function ownersCount(db: Db, orgId: string) {
  return (await authRepos(db).memberships.list(orgCtx(orgId), { role: 'owner' }, 200)).filter((m) => !m.revoked_at).length;
}

export async function updateMember(db: Db, auth: AuthContext, userId: string, b: z.output<typeof updateMemberBody>) {
  const m = await getMemberRow(db, auth, userId);
  if (!canAssign(auth.roles, m.role, m.venue_ids)) throw new AppError('FORBIDDEN', 403, 'Cannot manage this user');
  const role = b.role ?? m.role;
  const venueIds = b.venue_ids === undefined ? m.venue_ids : b.venue_ids?.length ? b.venue_ids : null;
  if (!canAssign(auth.roles, role, venueIds)) throw new AppError('FORBIDDEN', 403, 'Cannot assign this role');
  if (venueIds) await assertVenuesExist(db, orgCtx(auth.organizationId), venueIds);
  if (m.role === 'owner' && role !== 'owner' && (await ownersCount(db, auth.organizationId)) <= 1)
    throw new AppError('CONFLICT', 409, 'Organization must keep at least one owner');
  const n = (await authRepos(db).memberships.update(orgCtx(auth.organizationId), m.id, { role, venue_ids: role === 'owner' ? null : venueIds }))!;
  return memberDto(n, await authRepos(db).users.get(PLATFORM, userId));
}

export async function removeMember(db: Db, auth: AuthContext, userId: string) {
  const m = await getMemberRow(db, auth, userId);
  if (!canAssign(auth.roles, m.role, m.venue_ids)) throw new AppError('FORBIDDEN', 403, 'Cannot manage this user');
  if (m.role === 'owner' && (await ownersCount(db, auth.organizationId)) <= 1)
    throw new AppError('CONFLICT', 409, 'Organization must keep at least one owner');
  const r = authRepos(db);
  await r.memberships.update(orgCtx(auth.organizationId), m.id, { revoked_at: iso(now()) });
  const link = (await r.userOrgs.list(PLATFORM, { user_id: userId, org_id: auth.organizationId }, 1))[0];
  if (link) await r.userOrgs.update(PLATFORM, link.id, { revoked_at: iso(now()) });
  // Kill refresh tokens for this org immediately; access tokens die within accessTtl (≤ 15 min, Р-3).
  for (const t of await r.refresh.list(PLATFORM, { user_id: userId, org_id: auth.organizationId }, 200))
    if (!t.revoked_at) await r.refresh.update(PLATFORM, t.id, { revoked_at: iso(now()) });
}

export async function invite(db: Db, auth: AuthContext, b: z.output<typeof inviteBody>) {
  const venueIds = b.venue_ids?.length ? b.venue_ids : null;
  if (!canAssign(auth.roles, b.role, venueIds)) throw new AppError('FORBIDDEN', 403, 'Cannot assign this role');
  if (venueIds) await assertVenuesExist(db, orgCtx(auth.organizationId), venueIds);
  const c = authConfig();
  const secret = randomToken();
  const row = await authRepos(db).invitations.insert(orgCtx(auth.organizationId), {
    id: uuidv7(), email: b.email ?? null, phone: b.phone ?? null, role: b.role, venue_ids: venueIds, token_hash: sha256(secret),
    invited_by: auth.userId, expires_at: iso(now() + c.invitationTtlSec * 1000), accepted_at: null, revoked_at: null, created_at: iso(now()),
  });
  // Token embeds org + invitation id so acceptance needs no cross-tenant lookup. Delivery (SMS/e-mail/bot) is done by caller/notifications.
  return { id: row.id, role: row.role, venue_ids: row.venue_ids, email: row.email, phone: row.phone, expires_at: row.expires_at,
    token: `${auth.organizationId}.${row.id}.${secret}` };
}

export async function acceptInvitation(db: Db, b: z.output<typeof acceptInviteBody>): Promise<AuthResult> {
  const [orgId, invId, secret] = b.token.split('.');
  const bad = () => new AppError('NOT_FOUND', 404, 'Invitation not found or expired');
  if (!orgId || !invId || !secret || !/^[0-9a-f-]{36}$/.test(orgId) || !/^[0-9a-f-]{36}$/.test(invId)) throw bad();
  const r = authRepos(db);
  const inv: InvitationRow | null = await r.invitations.get(orgCtx(orgId), invId);
  if (!inv || !safeEqualHex(inv.token_hash, sha256(secret)) || inv.accepted_at || inv.revoked_at || Date.parse(inv.expires_at) <= now()) throw bad();
  let user = (inv.email && (await r.users.list(PLATFORM, { email: inv.email }, 1))[0]) ||
    (inv.phone && (await r.users.list(PLATFORM, { phone: inv.phone }, 1))[0]) || null;
  if (user?.password_hash && !(await verifyPassword(b.password ?? '', user.password_hash)))
    throw unauthorized('Existing account: confirm with your password');
  if (!user) {
    if (!b.password && !inv.phone) throw new AppError('VALIDATION_FAILED', 400, 'password is required', [{ field: 'password', message: 'Required' }]);
    user = await createUser(db, { email: inv.email, phone: inv.phone, full_name: b.full_name ?? null, password: b.password });
  }
  await addMembership(db, orgId, user.id, inv.role, inv.venue_ids);
  await r.invitations.update(orgCtx(orgId), inv.id, { accepted_at: iso(now()) });
  return finishLogin(db, user, orgId);
}
