import type { FastifyInstance, FastifyReply, FastifyRequest } from 'fastify';
import type { ZodTypeAny, z } from 'zod';
import { AppError } from '@podacha/shared';
import { MenuService, resolveLocale } from './service.js';
import { requireStaff, resolveQr } from './context.js';
import * as S from './schemas.js';

function parse<T extends ZodTypeAny>(schema: T, data: unknown): z.infer<T> {
  const r = schema.safeParse(data ?? {});
  if (!r.success) throw new AppError('VALIDATION_FAILED', 400, 'Validation failed',
    r.error.issues.map((i) => ({ field: '/' + i.path.join('/'), message: i.message })));
  return r.data;
}
const uuidRe = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function ids<K extends string>(req: FastifyRequest, ...keys: K[]): Record<K, string> {
  const p = req.params as Record<string, string>;
  for (const k of keys) if (!uuidRe.test(p[k] ?? '')) throw new AppError('NOT_FOUND', 404, `${k} not found`);
  return p as Record<K, string>;
}
function ifMatch(req: FastifyRequest): number | undefined {
  const h = req.headers['if-match'];
  if (typeof h !== 'string') return undefined;
  const m = /^(?:W\/)?"?v?(\d+)"?$/.exec(h.trim());
  if (!m) throw new AppError('VALIDATION_FAILED', 400, 'Malformed If-Match');
  return Number(m[1]);
}
const etagItem = (reply: FastifyReply, version: number) => reply.header('etag', `"v${version}"`);

export function registerMenuRoutes(app: FastifyInstance, svc: MenuService): void {
  const V = '/venues/:venueId';
  // Accept application/merge-patch+json like JSON.
  if (!app.hasContentTypeParser('application/merge-patch+json'))
    app.addContentTypeParser('application/merge-patch+json', { parseAs: 'string' }, (_req, body, done) => {
      try { done(null, body ? JSON.parse(body as string) : {}); } catch (e) { (e as { statusCode?: number }).statusCode = 400; done(e as Error, undefined); }
    });

  // ----- full admin menu -----
  app.get(`${V}/menu`, async (req) => {
    const { venueId } = ids(req, 'venueId');
    const { ctx } = requireStaff(req, venueId);
    const q = parse(S.publicMenuQuery, req.query);
    return svc.buildMenu(ctx, venueId, resolveLocale(q.locale, req.headers['accept-language']), true);
  });

  // ----- menus -----
  app.get(`${V}/menus`, async (req) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId);
    return { data: await svc.listMenus(ctx, venueId) };
  });
  app.post(`${V}/menus`, async (req, reply) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return reply.status(201).send(await svc.createMenu(ctx, venueId, parse(S.menuCreate, req.body)));
  });
  app.patch(`${V}/menus/:menuId`, async (req) => {
    const { venueId, menuId } = ids(req, 'venueId', 'menuId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return svc.updateMenu(ctx, venueId, menuId, parse(S.menuPatch, req.body));
  });
  app.delete(`${V}/menus/:menuId`, async (req, reply) => {
    const { venueId, menuId } = ids(req, 'venueId', 'menuId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    await svc.deleteMenu(ctx, venueId, menuId); return reply.status(204).send();
  });

  // ----- categories -----
  app.get(`${V}/menu/categories`, async (req) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId);
    return { data: await svc.listCategories(ctx, venueId) };
  });
  app.post(`${V}/menu/categories`, async (req, reply) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return reply.status(201).send(await svc.createCategory(ctx, venueId, parse(S.categoryCreate, req.body)));
  });
  app.patch(`${V}/menu/categories/:categoryId`, async (req) => {
    const { venueId, categoryId } = ids(req, 'venueId', 'categoryId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return svc.updateCategory(ctx, venueId, categoryId, parse(S.categoryPatch, req.body));
  });
  app.delete(`${V}/menu/categories/:categoryId`, async (req, reply) => {
    const { venueId, categoryId } = ids(req, 'venueId', 'categoryId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    await svc.deleteCategory(ctx, venueId, categoryId); return reply.status(204).send();
  });

  // ----- items -----
  app.get(`${V}/menu/items`, async (req) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId);
    return svc.listItems(ctx, venueId, parse(S.itemListQuery, req.query));
  });
  app.post(`${V}/menu/items`, async (req, reply) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    const item = await svc.createItem(ctx, venueId, parse(S.itemCreate, req.body));
    return etagItem(reply, item.version).status(201).send(item);
  });
  app.get(`${V}/menu/items/:itemId`, async (req, reply) => {
    const { venueId, itemId } = ids(req, 'venueId', 'itemId'); const { ctx } = requireStaff(req, venueId);
    const full = await svc.getAdminItem(ctx, venueId, itemId);
    return etagItem(reply, full.version).send(full);
  });
  app.patch(`${V}/menu/items/:itemId`, async (req, reply) => {
    const { venueId, itemId } = ids(req, 'venueId', 'itemId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    const item = await svc.updateItem(ctx, venueId, itemId, parse(S.itemPatch, req.body), ifMatch(req));
    return etagItem(reply, item.version).send(item);
  });
  app.delete(`${V}/menu/items/:itemId`, async (req, reply) => {
    const { venueId, itemId } = ids(req, 'venueId', 'itemId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    await svc.deleteItem(ctx, venueId, itemId); return reply.status(204).send();
  });
  app.put(`${V}/menu/items/:itemId/modifier-groups`, async (req) => {
    const { venueId, itemId } = ids(req, 'venueId', 'itemId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return svc.setItemGroups(ctx, venueId, itemId, parse(S.itemModifierGroupsPut, req.body).group_ids);
  });

  // ----- modifier groups & modifiers -----
  app.get(`${V}/menu/modifier-groups`, async (req) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId);
    return { data: await svc.listGroups(ctx, venueId) };
  });
  app.post(`${V}/menu/modifier-groups`, async (req, reply) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return reply.status(201).send(await svc.createGroup(ctx, venueId, parse(S.modifierGroupCreate, req.body)));
  });
  app.patch(`${V}/menu/modifier-groups/:groupId`, async (req) => {
    const { venueId, groupId } = ids(req, 'venueId', 'groupId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return svc.updateGroup(ctx, venueId, groupId, parse(S.modifierGroupPatch, req.body));
  });
  app.delete(`${V}/menu/modifier-groups/:groupId`, async (req, reply) => {
    const { venueId, groupId } = ids(req, 'venueId', 'groupId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    await svc.deleteGroup(ctx, venueId, groupId); return reply.status(204).send();
  });
  app.post(`${V}/menu/modifier-groups/:groupId/modifiers`, async (req, reply) => {
    const { venueId, groupId } = ids(req, 'venueId', 'groupId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return reply.status(201).send(await svc.createModifier(ctx, venueId, groupId, parse(S.modifierCreate, req.body)));
  });
  app.patch(`${V}/menu/modifiers/:modifierId`, async (req) => {
    const { venueId, modifierId } = ids(req, 'venueId', 'modifierId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return svc.updateModifier(ctx, venueId, modifierId, parse(S.modifierPatch, req.body));
  });
  app.delete(`${V}/menu/modifiers/:modifierId`, async (req, reply) => {
    const { venueId, modifierId } = ids(req, 'venueId', 'modifierId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    await svc.deleteModifier(ctx, venueId, modifierId); return reply.status(204).send();
  });

  // ----- translations -----
  app.get(`${V}/menu/translations`, async (req) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId);
    const q = req.query as { entity_type?: string; entity_id?: string };
    if (!q.entity_type || !q.entity_id || !uuidRe.test(q.entity_id)) throw new AppError('VALIDATION_FAILED', 400, 'entity_type and entity_id are required');
    return { data: await svc.listTranslations(ctx, venueId, q.entity_type, q.entity_id) };
  });
  app.put(`${V}/menu/translations`, async (req) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId, 'menu:write');
    return { data: await svc.putTranslations(ctx, venueId, parse(S.translationsPut, req.body).items) };
  });

  // ----- stop-list -----
  app.get(`${V}/stop-list`, async (req) => {
    const { venueId } = ids(req, 'venueId'); const { ctx } = requireStaff(req, venueId);
    return { data: await svc.activeStopList(ctx, venueId) };
  });
  app.put(`${V}/stop-list`, async (req) => {
    const { venueId } = ids(req, 'venueId'); const { ctx, principal } = requireStaff(req, venueId, 'stoplist:write');
    return { data: await svc.replaceStopList(ctx, venueId, parse(S.stopListPut, req.body).items, principal.userId) };
  });
  app.post(`${V}/stop-list`, async (req, reply) => {
    const { venueId } = ids(req, 'venueId'); const { ctx, principal } = requireStaff(req, venueId, 'stoplist:write');
    return reply.status(201).send(await svc.addStop(ctx, venueId, parse(S.stopListEntryInput, req.body), principal.userId));
  });
  // `{id}:lift` action suffix (api-contract.md §1). find-my-way can't mix a param with a literal colon
  // suffix reliably, so the whole segment is one param and parsed here.
  app.post(`${V}/stop-list/:entryAction`, async (req) => {
    const { venueId, entryAction } = req.params as { venueId: string; entryAction: string };
    const [entryId, action] = entryAction.split(':');
    if (action !== 'lift' || !uuidRe.test(entryId ?? '') || !uuidRe.test(venueId)) throw new AppError('NOT_FOUND', 404, 'Route not found');
    const { ctx } = requireStaff(req, venueId, 'stoplist:write');
    return svc.liftStop(ctx, venueId, entryId!);
  });

  // ----- public menu by table QR token (no auth; guest JWT flow lives in `guest` module) -----
  app.get('/public/menu/:qrToken', async (req, reply) => {
    const { qrToken } = req.params as { qrToken: string };
    const qr = await resolveQr(app, qrToken);
    const q = parse(S.publicMenuQuery, req.query);
    const menu = await svc.buildMenu({ organizationId: qr.organizationId }, qr.venueId, resolveLocale(q.locale, req.headers['accept-language']));
    const etag = `"${menu.version}"`;
    reply.header('etag', etag).header('cache-control', 'public, max-age=60').header('vary', 'Accept-Language');
    if (req.headers['if-none-match'] === etag) return reply.status(304).send();
    return { ...menu, table_id: qr.tableId };
  });
}
