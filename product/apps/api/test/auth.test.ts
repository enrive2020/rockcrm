import { describe, it, expect, afterAll, beforeAll } from 'vitest';
import { buildApp } from '../src/app.js';
import { configureAuth, authConfig } from '../src/modules/auth/config.js';
import { KeyStore, signJwt, verifyJwt } from '../src/modules/auth/jwt.js';
import { hashPassword, verifyPassword } from '../src/modules/auth/crypto.js';
import { canAssign, hasScope, grantOf } from '../src/modules/auth/rbac.js';

const sent: { phone: string; code: string }[] = [];
let clock = Date.now();
configureAuth({ otpSender: { async send(phone, code) { sent.push({ phone, code }); } }, now: () => clock });

const app = await buildApp();
afterAll(() => app.close());

const post = (url: string, payload: unknown, token?: string) =>
  app.inject({ method: 'POST', url: `/v1${url}`, payload: payload as object, headers: token ? { authorization: `Bearer ${token}` } : {} });
const get = (url: string, token?: string) => app.inject({ method: 'GET', url: `/v1${url}`, headers: token ? { authorization: `Bearer ${token}` } : {} });

let n = 0;
async function signup(extra: Record<string, unknown> = {}) {
  n++;
  const res = await post('/auth/signup', { email: `owner${n}@cafe.ru`, password: 'correct-horse-1', full_name: 'Иван Петров',
    organization: { legal_name: `ООО Кафе ${n}`, inn: String(7700000000 + n) }, pd_consent: true, ...extra });
  expect(res.statusCode).toBe(201);
  return res.json() as { access_token: string; refresh_token: string; organization_id: string; user: { id: string } };
}

describe('jwt', () => {
  const ks = KeyStore.generate();
  const o = { aud: 'admin', iss: 'x' };
  it('signs and verifies ES256', () => {
    const t = signJwt(ks, { iss: 'x', aud: 'admin', sub: 'u', exp: Math.floor(Date.now() / 1000) + 60 });
    expect(verifyJwt(ks, t, o).sub).toBe('u');
    expect(ks.jwks().keys[0]).toMatchObject({ kty: 'EC', crv: 'P-256', alg: 'ES256' });
  });
  it('rejects expired, tampered, foreign-key and wrong-aud tokens', () => {
    const t = signJwt(ks, { iss: 'x', aud: 'admin', sub: 'u', exp: Math.floor(Date.now() / 1000) - 120 });
    expect(() => verifyJwt(ks, t, o)).toThrow(/expired/);
    const good = signJwt(ks, { iss: 'x', aud: 'admin', sub: 'u', exp: Math.floor(Date.now() / 1000) + 60 });
    const [h, , s] = good.split('.');
    const forged = `${h}.${Buffer.from(JSON.stringify({ iss: 'x', aud: 'admin', sub: 'admin', exp: 9e9 })).toString('base64url')}.${s}`;
    expect(() => verifyJwt(ks, forged, o)).toThrow(/signature/);
    expect(() => verifyJwt(KeyStore.generate(), good, o)).toThrow(/Unknown signing key/);
    expect(() => verifyJwt(ks, good, { aud: 'guest', iss: 'x' })).toThrow(/audience/);
  });
});

describe('password hashing', () => {
  it('scrypt round-trip', async () => {
    const h = await hashPassword('secret-pass-1');
    expect(await verifyPassword('secret-pass-1', h)).toBe(true);
    expect(await verifyPassword('wrong', h)).toBe(false);
    expect(await verifyPassword('x', null)).toBe(false);
  });
});

describe('rbac', () => {
  const v1 = 'v1', v2 = 'v2';
  it('venue-bound manager only has scopes on own venues', () => {
    const roles = [grantOf('manager', [v1])];
    expect(hasScope(roles, 'menu:write', v1)).toBe(true);
    expect(hasScope(roles, 'menu:write', v2)).toBe(false);
    expect(hasScope(roles, 'venues:create')).toBe(false);
  });
  it('assignment rules', () => {
    expect(canAssign([grantOf('owner', null)], 'owner', null)).toBe(true);
    expect(canAssign([grantOf('admin', null)], 'owner', null)).toBe(false);
    expect(canAssign([grantOf('admin', null)], 'manager', [v1])).toBe(true);
    expect(canAssign([grantOf('manager', [v1])], 'waiter', [v1])).toBe(true);
    expect(canAssign([grantOf('manager', [v1])], 'waiter', [v2])).toBe(false);
    expect(canAssign([grantOf('manager', [v1])], 'waiter', null)).toBe(false);
    expect(canAssign([grantOf('manager', [v1])], 'manager', [v1])).toBe(false);
    expect(canAssign([grantOf('waiter', [v1])], 'waiter', [v1])).toBe(false);
  });
});

describe('auth flows', () => {
  it('signup → /me', async () => {
    const s = await signup();
    const me = await get('/me', s.access_token);
    expect(me.statusCode).toBe(200);
    expect(me.json()).toMatchObject({ organization_id: s.organization_id, roles: [{ r: 'owner', v: '*' }] });
    expect(me.json().organizations).toHaveLength(1);
  });

  it('signup validation and duplicates', async () => {
    const bad = await post('/auth/signup', { email: 'x@y.ru', password: 'short', full_name: 'a', organization: { legal_name: 'a', inn: '1' }, pd_consent: true });
    expect(bad.statusCode).toBe(400);
    expect(bad.json().code).toBe('VALIDATION_FAILED');
    const noConsent = await post('/auth/signup', { email: 'z@y.ru', password: 'long-enough-1', full_name: 'a', organization: { legal_name: 'a', inn: '7700000000' } });
    expect(noConsent.statusCode).toBe(400);
    await signup({ email: 'dup@cafe.ru' });
    const dup = await post('/auth/signup', { email: 'DUP@cafe.ru', password: 'correct-horse-1', full_name: 'x',
      organization: { legal_name: 'b', inn: '7799999999' }, pd_consent: true });
    expect(dup.statusCode).toBe(409);
  });

  it('login with password; wrong password → 401', async () => {
    await signup({ email: 'login@cafe.ru' });
    const ok = await post('/auth/login', { login: 'Login@Cafe.ru', password: 'correct-horse-1' });
    expect(ok.statusCode).toBe(200);
    expect(ok.json().access_token).toBeTruthy();
    expect((await post('/auth/login', { login: 'login@cafe.ru', password: 'nope-nope-nope' })).statusCode).toBe(401);
    expect((await post('/auth/login', { login: 'ghost@cafe.ru', password: 'nope-nope-nope' })).json().code).toBe('UNAUTHORIZED');
  });

  it('requires bearer token', async () => {
    expect((await get('/me')).statusCode).toBe(401);
    expect((await get('/me', 'garbage')).statusCode).toBe(401);
  });

  it('access token expires after 15 minutes', async () => {
    const s = await signup();
    clock += 16 * 60 * 1000;
    expect((await get('/me', s.access_token)).statusCode).toBe(401);
    const r = await post('/auth/refresh', { refresh_token: s.refresh_token });
    expect(r.statusCode).toBe(200);
    expect((await get('/me', r.json().access_token)).statusCode).toBe(200);
  });

  it('refresh rotation and reuse detection revokes family', async () => {
    const s = await signup();
    const r1 = await post('/auth/refresh', { refresh_token: s.refresh_token });
    expect(r1.statusCode).toBe(200);
    const reuse = await post('/auth/refresh', { refresh_token: s.refresh_token });
    expect(reuse.statusCode).toBe(401);
    // whole chain is now revoked
    expect((await post('/auth/refresh', { refresh_token: r1.json().refresh_token })).statusCode).toBe(401);
  });

  it('logout revokes refresh token', async () => {
    const s = await signup();
    expect((await post('/auth/logout', { refresh_token: s.refresh_token })).statusCode).toBe(204);
    expect((await post('/auth/refresh', { refresh_token: s.refresh_token })).statusCode).toBe(401);
  });

  it('OTP login by phone, attempts limit, rate limit', async () => {
    await signup({ email: undefined, phone: '8 (916) 123-45-67' });
    const rq = await post('/auth/otp:request', { phone: '+79161234567' });
    expect(rq.statusCode).toBe(202);
    const code = sent.at(-1)!.code;
    expect(sent.at(-1)!.phone).toBe('+79161234567');
    expect((await post('/auth/otp:verify', { phone: '+79161234567', code: code === '000000' ? '111111' : '000000' })).statusCode).toBe(401);
    const ok = await post('/auth/otp:verify', { phone: '+79161234567', code });
    expect(ok.statusCode).toBe(200);
    expect((await post('/auth/otp:verify', { phone: '+79161234567', code })).statusCode).toBe(401); // single-use
    // unknown phone: same 202, nothing sent
    const before = sent.length;
    expect((await post('/auth/otp:request', { phone: '+79990000000' })).statusCode).toBe(202);
    expect(sent.length).toBe(before);
    for (let i = 0; i < 4; i++) await post('/auth/otp:request', { phone: '+79990000001' });
    await post('/auth/otp:request', { phone: '+79990000001' });
    expect((await post('/auth/otp:request', { phone: '+79990000001' })).statusCode).toBe(429);
  });

  it('jwks is published', async () => {
    const r = await get('/.well-known/jwks.json');
    expect(r.statusCode).toBe(200);
    expect(r.json().keys[0].kid).toBe(authConfig().keys.current.kid);
  });
});

describe('users & invitations', () => {
  let owner: Awaited<ReturnType<typeof signup>>;
  let venueA: string, venueB: string;
  beforeAll(async () => {
    owner = await signup();
    venueA = (await post('/venues', { name: 'Кафе на Арбате' }, owner.access_token)).json().id;
    venueB = (await post('/venues', { name: 'Кафе на Тверской' }, owner.access_token)).json().id;
  });

  async function inviteAndAccept(token: string, body: Record<string, unknown>, email: string) {
    const inv = await post('/users/invitations', { email, ...body }, token);
    expect(inv.statusCode).toBe(201);
    const acc = await post('/auth/invitations:accept', { token: inv.json().token, password: 'invitee-pass-1', full_name: 'Сотрудник' });
    expect(acc.statusCode).toBe(200);
    return acc.json() as { access_token: string; user: { id: string } };
  }

  it('owner invites manager of venue A; manager invites waiter in A but not B', async () => {
    const mgr = await inviteAndAccept(owner.access_token, { role: 'manager', venue_ids: [venueA] }, 'mgr@cafe.ru');
    const me = (await get('/me', mgr.access_token)).json();
    expect(me.roles).toEqual([{ r: 'manager', v: [venueA] }]);

    const waiter = await inviteAndAccept(mgr.access_token, { role: 'waiter', venue_ids: [venueA] }, 'waiter@cafe.ru');
    expect((await post('/users/invitations', { email: 'w2@cafe.ru', role: 'waiter', venue_ids: [venueB] }, mgr.access_token)).statusCode).toBe(403);
    expect((await post('/users/invitations', { email: 'm2@cafe.ru', role: 'manager', venue_ids: [venueA] }, mgr.access_token)).statusCode).toBe(403);

    // manager sees only staff of own venue; owner sees all
    const mList = (await get('/users', mgr.access_token)).json().data as { id: string }[];
    expect(mList.map((u) => u.id).sort()).toEqual([mgr.user.id, waiter.user.id].sort());
    expect(((await get('/users', owner.access_token)).json().data as unknown[]).length).toBe(3);

    // waiter cannot manage anyone
    expect((await post('/users/invitations', { email: 'x@cafe.ru', role: 'waiter', venue_ids: [venueA] }, waiter.access_token)).statusCode).toBe(403);
    // manager cannot touch the owner (invisible → 404)
    const del = await app.inject({ method: 'DELETE', url: `/v1/users/${owner.user.id}`, headers: { authorization: `Bearer ${mgr.access_token}` } });
    expect(del.statusCode).toBe(404);
  });

  it('invitation token is single-use and venue ids must exist in tenant', async () => {
    const inv = await post('/users/invitations', { email: 'once@cafe.ru', role: 'cook', venue_ids: [venueA] }, owner.access_token);
    const t = inv.json().token;
    expect((await post('/auth/invitations:accept', { token: t, password: 'invitee-pass-1' })).statusCode).toBe(200);
    expect((await post('/auth/invitations:accept', { token: t, password: 'invitee-pass-1' })).statusCode).toBe(404);
    const other = await signup();
    const foreignVenue = (await post('/venues', { name: 'Чужое' }, other.access_token)).json().id;
    expect((await post('/users/invitations', { email: 'f@cafe.ru', role: 'waiter', venue_ids: [foreignVenue] }, owner.access_token)).statusCode).toBe(404);
  });

  it('cannot remove or demote the last owner; removal revokes refresh tokens', async () => {
    const del = await app.inject({ method: 'DELETE', url: `/v1/users/${owner.user.id}`, headers: { authorization: `Bearer ${owner.access_token}` } });
    expect(del.statusCode).toBe(409);
    const cook = await inviteAndAccept(owner.access_token, { role: 'cook', venue_ids: [venueB] }, 'cook2@cafe.ru');
    const login = (await post('/auth/login', { login: 'cook2@cafe.ru', password: 'invitee-pass-1' })).json();
    const rm = await app.inject({ method: 'DELETE', url: `/v1/users/${cook.user.id}`, headers: { authorization: `Bearer ${owner.access_token}` } });
    expect(rm.statusCode).toBe(204);
    expect((await post('/auth/refresh', { refresh_token: login.refresh_token })).statusCode).toBe(401);
    expect((await post('/auth/login', { login: 'cook2@cafe.ru', password: 'invitee-pass-1' })).statusCode).toBe(401);
  });

  it('user in two orgs can switch organization', async () => {
    const other = await signup();
    // invite the other org's owner as accountant into first org (existing account must confirm password)
    const inv2 = await post('/users/invitations', { email: (await get('/me', other.access_token)).json().user.email, role: 'accountant' }, owner.access_token);
    expect(inv2.statusCode).toBe(201);
    const noPw = await post('/auth/invitations:accept', { token: inv2.json().token });
    expect(noPw.statusCode).toBe(401);
    const acc = await post('/auth/invitations:accept', { token: inv2.json().token, password: 'correct-horse-1' });
    expect(acc.statusCode).toBe(200);
    expect(acc.json().organization_id).toBe(owner.organization_id);
    const sw = await post('/auth/switch-organization', { organization_id: other.organization_id }, acc.json().access_token);
    expect(sw.statusCode).toBe(200);
    expect((await get('/me', sw.json().access_token)).json().roles).toEqual([{ r: 'owner', v: '*' }]);
    const stranger = await signup();
    expect((await post('/auth/switch-organization', { organization_id: stranger.organization_id }, sw.json().access_token)).statusCode).toBe(401);
  });
});
