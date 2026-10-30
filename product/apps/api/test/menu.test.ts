import { describe, it, expect, afterAll, beforeAll } from 'vitest';
import { randomUUID } from 'node:crypto';
import { createMemoryDb } from '@podacha/db';
import Fastify from 'fastify';
import { AppError } from '@podacha/shared';
import menuPlugin from '../src/modules/menu/index.js';
import { setQrResolver } from '../src/modules/menu/context.js';
import { getMenuService, isStopActive, availabilityOf, resolveLocale } from '../src/modules/menu/service.js';

const db = createMemoryDb();
// Isolated app (only the menu module) so parallel work in other modules can't break these tests.
const app = Fastify();
app.decorate('db', db);
app.setErrorHandler((err, req, reply) => {
  if (err instanceof AppError) return reply.status(err.status).type('application/problem+json').send(err.toProblem(req.id));
  return reply.status((err as { statusCode?: number }).statusCode ?? 500).send({ code: 'INTERNAL', message: err.message });
});
await app.register(menuPlugin, { prefix: '/v1' });
await app.ready();
afterAll(() => app.close());

const ORG = randomUUID(); const OTHER = randomUUID();
const VENUE = randomUUID(); const TABLE = randomUUID();
const H = { 'x-org-id': ORG, 'x-user-id': randomUUID() };
const base = `/v1/venues/${VENUE}`;
const req = (method: string, url: string, payload?: unknown, headers: Record<string, string> = H) =>
  app.inject({ method: method as 'GET', url, payload: payload as object, headers });

setQrResolver(async (t) => {
  if (t === 'goodtoken_abcdef') return { organizationId: ORG, venueId: VENUE, tableId: TABLE, isActive: true };
  if (t === 'revokedtoken_abc') return { organizationId: ORG, venueId: VENUE, tableId: TABLE, isActive: false };
  return null;
});

let menuId = ''; let catId = ''; let subCatId = ''; let burger = ''; let cola = ''; let groupId = ''; let cheese = ''; let bacon = '';

beforeAll(async () => {
  menuId = (await req('POST', `${base}/menus`, { name: 'Основное' })).json().id;
  catId = (await req('POST', `${base}/menu/categories`, { menu_id: menuId, name: 'Бургеры', sort: 1 })).json().id;
  subCatId = (await req('POST', `${base}/menu/categories`, { menu_id: menuId, name: 'Напитки', sort: 2 })).json().id;
  const b = await req('POST', `${base}/menu/items`, { category_id: catId, name: 'Чизбургер', price: 45000, allergens: ['gluten', 'milk'], may_contain: ['sesame'], tags: ['хит'] });
  expect(b.statusCode).toBe(201);
  burger = b.json().id;
  cola = (await req('POST', `${base}/menu/items`, { category_id: subCatId, name: 'Кола', price: 15000 })).json().id;
  groupId = (await req('POST', `${base}/menu/modifier-groups`, { name: 'Добавки', min_select: 0, max_select: 2 })).json().id;
  cheese = (await req('POST', `${base}/menu/modifier-groups/${groupId}/modifiers`, { name: 'Сыр', price_delta: 5000, sort: 1 })).json().id;
  bacon = (await req('POST', `${base}/menu/modifier-groups/${groupId}/modifiers`, { name: 'Бекон', price_delta: 8000, sort: 2 })).json().id;
  expect((await req('PUT', `${base}/menu/items/${burger}/modifier-groups`, { group_ids: [groupId] })).statusCode).toBe(200);
});

describe('menu: admin CRUD', () => {
  it('requires auth', async () => {
    const r = await req('GET', `${base}/menus`, undefined, {});
    expect(r.statusCode).toBe(401);
    expect(r.json().code).toBe('UNAUTHORIZED');
  });
  it('requires menu:write scope for mutations', async () => {
    const r = await req('POST', `${base}/menus`, { name: 'x' }, { ...H, 'x-scopes': 'stoplist:write' });
    expect(r.statusCode).toBe(403);
  });
  it('validates input (kopecks must be integer)', async () => {
    const r = await req('POST', `${base}/menu/items`, { category_id: catId, name: 'X', price: 10.5 });
    expect(r.statusCode).toBe(400);
    expect(r.json().code).toBe('VALIDATION_FAILED');
    expect(r.json().errors[0].field).toBe('/price');
  });
  it('returns item with allergens, groups and ETag', async () => {
    const r = await req('GET', `${base}/menu/items/${burger}`);
    expect(r.statusCode).toBe(200);
    expect(r.headers.etag).toBe('"v2"');
    expect(r.json()).toMatchObject({ name: 'Чизбургер', price: 45000, allergens: ['gluten', 'milk'], may_contain: ['sesame'], modifier_group_ids: [groupId], hidden: false });
  });
  it('lists, filters and paginates items', async () => {
    const all = (await req('GET', `${base}/menu/items?limit=1`)).json();
    expect(all.data).toHaveLength(1);
    expect(all.page.has_more).toBe(true);
    const next = (await req('GET', `${base}/menu/items?limit=1&cursor=${all.page.next_cursor}`)).json();
    expect(next.data[0].id).not.toBe(all.data[0].id);
    expect((await req('GET', `${base}/menu/items?q=кол`)).json().data.map((i: { id: string }) => i.id)).toEqual([cola]);
    expect((await req('GET', `${base}/menu/items?category_id=${catId}`)).json().data).toHaveLength(1);
  });
  it('patches with If-Match optimistic locking', async () => {
    const stale = await app.inject({ method: 'PATCH', url: `${base}/menu/items/${cola}`, headers: { ...H, 'if-match': '"v99"' }, payload: { price: 16000 } });
    expect(stale.statusCode).toBe(412);
    const ok = await app.inject({ method: 'PATCH', url: `${base}/menu/items/${cola}`, headers: { ...H, 'if-match': '"v1"', 'content-type': 'application/merge-patch+json' }, payload: JSON.stringify({ price: 16000, allergens: [] }) });
    expect(ok.statusCode).toBe(200);
    expect(ok.json()).toMatchObject({ price: 16000, version: 2 });
    expect(ok.headers.etag).toBe('"v2"');
  });
  it('prevents category cycles and deleting non-empty categories', async () => {
    const child = (await req('POST', `${base}/menu/categories`, { menu_id: menuId, parent_id: catId, name: 'Острые' })).json();
    expect((await req('PATCH', `${base}/menu/categories/${catId}`, { parent_id: child.id })).statusCode).toBe(400);
    expect((await req('DELETE', `${base}/menu/categories/${catId}`)).statusCode).toBe(409);
    expect((await req('DELETE', `${base}/menu/categories/${child.id}`)).statusCode).toBe(204);
  });
  it('modifier group validation and update', async () => {
    expect((await req('POST', `${base}/menu/modifier-groups`, { name: 'bad', min_select: 3, max_select: 1 })).statusCode).toBe(400);
    expect((await req('PATCH', `${base}/menu/modifier-groups/${groupId}`, { min_select: 5 })).statusCode).toBe(400);
    const m = await req('PATCH', `${base}/menu/modifiers/${bacon}`, { price_delta: 9000 });
    expect(m.json().price_delta).toBe(9000);
  });
  it('soft-deletes items', async () => {
    const tmp = (await req('POST', `${base}/menu/items`, { category_id: catId, name: 'Временный', price: 100 })).json().id;
    expect((await req('DELETE', `${base}/menu/items/${tmp}`)).statusCode).toBe(204);
    expect((await req('GET', `${base}/menu/items/${tmp}`)).statusCode).toBe(404);
    const since = (await req('GET', `${base}/menu/items?updated_since=2000-01-01T00:00:00Z`)).json().data;
    expect(since.find((i: { id: string }) => i.id === tmp)?.deleted_at).toBeTruthy();
  });
  it('isolates tenants: other org gets 404', async () => {
    const r = await req('GET', `${base}/menu/items/${burger}`, undefined, { 'x-org-id': OTHER });
    expect(r.statusCode).toBe(404);
    const p = await req('PATCH', `${base}/menu/items/${burger}`, { price: 1 }, { 'x-org-id': OTHER });
    expect(p.statusCode).toBe(404);
  });
  it('rejects edits to POS-owned fields with MENU_MANAGED_BY_POS', async () => {
    const ctx = { organizationId: ORG };
    await db.repo('menu_item').update(ctx, cola, { external_source: 'iiko', external_id: 'iiko-1' });
    const r = await req('PATCH', `${base}/menu/items/${cola}`, { price: 1 });
    expect(r.statusCode).toBe(409);
    expect(r.json().code).toBe('MENU_MANAGED_BY_POS');
    expect((await req('PATCH', `${base}/menu/items/${cola}`, { description: 'Охлаждённая', tags: ['холодное'] })).statusCode).toBe(200);
    await db.repo('menu_item').update(ctx, cola, { external_source: null, external_id: null });
  });
});

describe('menu: translations', () => {
  it('rejects non-translatable fields', async () => {
    const r = await req('PUT', `${base}/menu/translations`, { items: [{ entity_type: 'menu_item', entity_id: burger, field: 'price', locale: 'en', text: 'x' }] });
    expect(r.statusCode).toBe(400);
  });
  it('upserts and lists translations', async () => {
    const items = [
      { entity_type: 'menu_item', entity_id: burger, field: 'name', locale: 'en', text: 'Cheeseburger' },
      { entity_type: 'menu_category', entity_id: catId, field: 'name', locale: 'en', text: 'Burgers' },
      { entity_type: 'modifier', entity_id: cheese, field: 'name', locale: 'en', text: 'Cheese', status: 'machine' },
    ];
    expect((await req('PUT', `${base}/menu/translations`, { items })).statusCode).toBe(200);
    await req('PUT', `${base}/menu/translations`, { items: [{ ...items[0], text: 'Cheese burger' }] });
    const list = (await req('GET', `${base}/menu/translations?entity_type=menu_item&entity_id=${burger}`)).json().data;
    expect(list).toHaveLength(1);
    expect(list[0].text).toBe('Cheese burger');
  });
});

describe('menu: stop-list and public menu', () => {
  it('public menu by QR token applies translations, ETag and 304', async () => {
    const r = await app.inject({ method: 'GET', url: '/v1/public/menu/goodtoken_abcdef?locale=en' });
    expect(r.statusCode).toBe(200);
    const m = r.json();
    expect(m.locale).toBe('en');
    expect(m.table_id).toBe(TABLE);
    expect(m.categories[0]).toMatchObject({ name: 'Burgers' });
    const b = m.categories[0].items[0];
    expect(b).toMatchObject({ name: 'Cheese burger', available: true, allergens: ['gluten', 'milk'] });
    expect(b.modifier_groups[0].options.map((o: { name: string }) => o.name)).toEqual(['Cheese', 'Бекон']);
    const again = await app.inject({ method: 'GET', url: '/v1/public/menu/goodtoken_abcdef?locale=en', headers: { 'if-none-match': r.headers.etag as string } });
    expect(again.statusCode).toBe(304);
    const ru = (await app.inject({ method: 'GET', url: '/v1/public/menu/goodtoken_abcdef', headers: { 'accept-language': 'ru-RU,ru;q=0.9' } })).json();
    expect(ru.categories[0].items[0].name).toBe('Чизбургер');
  });
  it('unknown QR → 404, revoked → 410 QR_REVOKED', async () => {
    expect((await app.inject({ method: 'GET', url: '/v1/public/menu/unknowntoken_x' })).statusCode).toBe(404);
    const r = await app.inject({ method: 'GET', url: '/v1/public/menu/revokedtoken_abc' });
    expect(r.statusCode).toBe(410);
    expect(r.json().code).toBe('QR_REVOKED');
  });
  it('stop-list marks item unavailable and changes menu version; lift restores', async () => {
    const before = (await app.inject({ method: 'GET', url: '/v1/public/menu/goodtoken_abcdef' })).json();
    const add = await req('POST', `${base}/stop-list`, { item_id: burger, reason: 'нет булок' });
    expect(add.statusCode).toBe(201);
    const after = (await app.inject({ method: 'GET', url: '/v1/public/menu/goodtoken_abcdef' })).json();
    expect(after.version).not.toBe(before.version);
    expect(after.categories.find((c: { id: string }) => c.id === catId).items[0].available).toBe(false);
    const unavailable = await getMenuService(db).checkAvailability({ organizationId: ORG }, VENUE, [{ item_id: burger }, { item_id: cola }]);
    expect(unavailable).toEqual([{ item_id: burger, reason: 'STOP_LIST' }]);
    const lift = await req('POST', `${base}/stop-list/${add.json().id}:lift`);
    expect(lift.statusCode).toBe(200);
    expect(lift.json().lifted_at).toBeTruthy();
    expect((await req('GET', `${base}/stop-list`)).json().data).toHaveLength(0);
  });
  it('PUT stop-list replaces manual entries; balance > 0 keeps item available', async () => {
    await req('PUT', `${base}/stop-list`, { items: [{ item_id: burger }, { modifier_id: bacon }] });
    let r = await req('PUT', `${base}/stop-list`, { items: [{ item_id: cola, balance: 3 }] });
    expect(r.statusCode).toBe(200);
    const active = (await req('GET', `${base}/stop-list`)).json().data;
    expect(active).toHaveLength(1);
    const m = (await app.inject({ method: 'GET', url: '/v1/public/menu/goodtoken_abcdef' })).json();
    const colaDto = m.categories.flatMap((c: { items: unknown[] }) => c.items).find((i: { id: string }) => i.id === cola);
    expect(colaDto).toMatchObject({ available: true, remaining_qty: 3 });
    r = await req('PUT', `${base}/stop-list`, { items: [{ item_id: burger, modifier_id: bacon }] });
    expect(r.statusCode).toBe(400);
    expect((await req('PUT', `${base}/stop-list`, { items: [] }, { ...H, 'x-scopes': 'menu:write' })).statusCode).toBe(403);
  });
  it('hidden items and inactive menus are excluded from guest menu but visible in admin menu', async () => {
    await req('PATCH', `${base}/menu/items/${cola}`, { hidden: true });
    const pub = (await app.inject({ method: 'GET', url: '/v1/public/menu/goodtoken_abcdef' })).json();
    expect(pub.categories.find((c: { id: string }) => c.id === subCatId)).toBeUndefined();
    const admin = (await req('GET', `${base}/menu`)).json();
    expect(admin.categories.find((c: { id: string }) => c.id === subCatId).items[0].id).toBe(cola);
    await req('PATCH', `${base}/menus/${menuId}`, { is_active: false });
    expect((await app.inject({ method: 'GET', url: '/v1/public/menu/goodtoken_abcdef' })).json().categories).toEqual([]);
    await req('PATCH', `${base}/menus/${menuId}`, { is_active: true });
  });
});

describe('menu: helpers', () => {
  it('isStopActive respects window and lift', () => {
    const now = Date.parse('2026-01-01T12:00:00Z');
    expect(isStopActive({ lifted_at: null, valid_from: '2026-01-01T00:00:00Z', valid_until: null }, now)).toBe(true);
    expect(isStopActive({ lifted_at: null, valid_from: '2026-01-01T00:00:00Z', valid_until: '2026-01-01T11:00:00Z' }, now)).toBe(false);
    expect(isStopActive({ lifted_at: '2026-01-01T01:00:00Z', valid_from: '2026-01-01T00:00:00Z', valid_until: null }, now)).toBe(false);
  });
  it('availabilityOf and resolveLocale', () => {
    expect(availabilityOf([])).toEqual({ available: true, remaining: null });
    expect(resolveLocale(undefined, 'en-US,en;q=0.8')).toBe('en');
    expect(resolveLocale('zh', 'en')).toBe('zh');
    expect(resolveLocale()).toBe('ru');
  });
});
