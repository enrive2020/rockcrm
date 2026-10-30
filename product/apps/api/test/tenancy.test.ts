import { describe, it, expect, afterAll } from 'vitest';
import { buildApp } from '../src/app.js';
import { configureAuth } from '../src/modules/auth/config.js';
import { slugify } from '../src/modules/tenancy/service.js';

configureAuth({ otpSender: { async send() {} } });
const app = await buildApp();
afterAll(() => app.close());

const req = (method: 'GET' | 'POST' | 'PATCH' | 'DELETE', url: string, token?: string, payload?: unknown) =>
  app.inject({ method, url: `/v1${url}`, payload: payload as object, headers: token ? { authorization: `Bearer ${token}` } : {} });

let n = 0;
async function owner() {
  n++;
  const r = await req('POST', '/auth/signup', undefined, { email: `t${n}@bar.ru`, password: 'correct-horse-1', full_name: 'O',
    organization: { legal_name: `ООО Бар ${n}`, inn: String(5000000000 + n) }, pd_consent: true });
  return r.json() as { access_token: string; organization_id: string };
}

describe('slugify', () => {
  it('transliterates Russian', () => { expect(slugify('Кафе «Пушкин» №1')).toBe('kafe-pushkin-1'); });
});

describe('organization', () => {
  it('get and update own organization', async () => {
    const o = await owner();
    expect((await req('GET', '/organization', o.access_token)).json()).toMatchObject({ id: o.organization_id, status: 'trial' });
    const p = await req('PATCH', '/organization', o.access_token, { tax_system: 'osn' });
    expect(p.statusCode).toBe(200);
    expect(p.json().tax_system).toBe('osn');
  });
});

describe('venues + tenant isolation', () => {
  it('CRUD', async () => {
    const o = await owner();
    const c = await req('POST', '/venues', o.access_token, { name: 'Бар Луна', timezone: 'Asia/Yekaterinburg', service_charge_bps: 1000 });
    expect(c.statusCode).toBe(201);
    const v = c.json();
    expect(v).toMatchObject({ slug: 'bar-luna', timezone: 'Asia/Yekaterinburg', currency: 'RUB' });
    const c2 = await req('POST', '/venues', o.access_token, { name: 'Бар Луна' });
    expect(c2.json().slug).toMatch(/^bar-luna-[0-9a-f]{4}$/);
    expect((await req('POST', '/venues', o.access_token, { name: 'X', slug: 'bar-luna' })).statusCode).toBe(409);
    expect((await req('POST', '/venues', o.access_token, { name: 'X', timezone: 'Mars/Base' })).statusCode).toBe(400);
    expect((await req('GET', '/venues', o.access_token)).json().data).toHaveLength(2);
    const u = await req('PATCH', `/venues/${v.id}`, o.access_token, { name: 'Луна', slug: 'luna-bar' });
    expect(u.json()).toMatchObject({ name: 'Луна', slug: 'luna-bar' });
    expect((await req('DELETE', `/venues/${v.id}`, o.access_token)).statusCode).toBe(204);
    expect((await req('GET', `/venues/${v.id}`, o.access_token)).statusCode).toBe(404);
    expect((await req('GET', '/venues', o.access_token)).json().data).toHaveLength(1);
  });

  it('foreign tenant venue returns 404 for read, update and delete', async () => {
    const a = await owner(); const b = await owner();
    const v = (await req('POST', '/venues', a.access_token, { name: 'Чужая точка' })).json();
    expect((await req('GET', `/venues/${v.id}`, b.access_token)).statusCode).toBe(404);
    expect((await req('PATCH', `/venues/${v.id}`, b.access_token, { name: 'hack' })).statusCode).toBe(404);
    expect((await req('DELETE', `/venues/${v.id}`, b.access_token)).statusCode).toBe(404);
    expect((await req('GET', '/venues', b.access_token)).json().data).toHaveLength(0);
    expect((await req('GET', `/venues/${v.id}`, a.access_token)).json().name).toBe('Чужая точка');
  });

  it('manager sees only assigned venues, cannot create; waiter cannot edit', async () => {
    const o = await owner();
    const va = (await req('POST', '/venues', o.access_token, { name: 'Точка А' })).json().id;
    const vb = (await req('POST', '/venues', o.access_token, { name: 'Точка Б' })).json().id;
    const inv = await req('POST', '/users/invitations', o.access_token, { email: `m${n}@bar.ru`, role: 'manager', venue_ids: [va] });
    const mgr = (await req('POST', '/auth/invitations:accept', undefined, { token: inv.json().token, password: 'manager-pass-1' })).json();
    const list = (await req('GET', '/venues', mgr.access_token)).json().data as { id: string }[];
    expect(list.map((x) => x.id)).toEqual([va]);
    expect((await req('GET', `/venues/${vb}`, mgr.access_token)).statusCode).toBe(404);
    expect((await req('PATCH', `/venues/${va}`, mgr.access_token, { address: 'Москва' })).statusCode).toBe(200);
    expect((await req('POST', '/venues', mgr.access_token, { name: 'Новая' })).statusCode).toBe(403);
    expect((await req('PATCH', '/organization', mgr.access_token, { legal_name: 'x' })).statusCode).toBe(403);

    const winv = await req('POST', '/users/invitations', mgr.access_token, { email: `w${n}@bar.ru`, role: 'waiter', venue_ids: [va] });
    const w = (await req('POST', '/auth/invitations:accept', undefined, { token: winv.json().token, password: 'waiter-pass-1' })).json();
    expect((await req('GET', `/venues/${va}`, w.access_token)).statusCode).toBe(200);
    expect((await req('PATCH', `/venues/${va}`, w.access_token, { address: 'x' })).statusCode).toBe(403);
  });
});
