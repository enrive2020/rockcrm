import { createHash } from 'node:crypto';
import type { Db, TenantContext } from '@podacha/db';
import {
  AppError, uuidv7, type AllergenCode, type PublicMenu, type PublicMenuCategory, type PublicMenuItem,
  type PublicModifierGroup,
} from '@podacha/shared';
import {
  MenuRepo, type CategoryRow, type GroupRow, type ItemRow, type MenuRow, type ModifierRow, type StopRow,
  type TranslationRow,
} from './repo.js';
import type { ItemCreate, ItemPatch, StopListInput } from './schemas.js';

export const DEFAULT_LOCALE = 'ru';
/** Fields a POS-synced item keeps authoritative (integrations.md: «меню из POS — источник истины»). */
const POS_OWNED_FIELDS = ['name', 'price', 'category_id', 'vat_rate', 'marking_required'] as const;

const notFound = (what: string) => new AppError('NOT_FOUND', 404, `${what} not found`);
const nowIso = () => new Date().toISOString();

/** A stop-list entry is active if not lifted and inside its validity window. */
export function isStopActive(e: Pick<StopRow, 'lifted_at' | 'valid_from' | 'valid_until'>, now = Date.now()): boolean {
  return !e.lifted_at && Date.parse(e.valid_from) <= now && (!e.valid_until || Date.parse(e.valid_until) > now);
}
/** Availability derived from active entries: null/<=0 remaining → unavailable. */
export function availabilityOf(entries: StopRow[]): { available: boolean; remaining: number | null } {
  if (!entries.length) return { available: true, remaining: null };
  if (entries.some((e) => e.remaining_qty === null || e.remaining_qty <= 0)) return { available: false, remaining: 0 };
  return { available: true, remaining: Math.min(...entries.map((e) => e.remaining_qty!)) };
}

/** Pick locale: explicit ?locale → first Accept-Language tag → ru. */
export function resolveLocale(query?: string, acceptLanguage?: string): string {
  if (query) return query;
  const first = acceptLanguage?.split(',')[0]?.split(';')[0]?.trim();
  if (first && /^[a-zA-Z]{2}/.test(first)) return first.slice(0, 2).toLowerCase();
  return DEFAULT_LOCALE;
}

export interface MenuServiceOptions { mediaBaseUrl?: string; now?: () => number }

export class MenuService {
  readonly repo: MenuRepo;
  private readonly mediaBaseUrl: string;
  private readonly now: () => number;
  constructor(db: Db, opts: MenuServiceOptions = {}) {
    this.repo = new MenuRepo(db);
    this.mediaBaseUrl = opts.mediaBaseUrl ?? process.env.MEDIA_BASE_URL ?? 'https://media.podacha.ai';
    this.now = opts.now ?? Date.now;
  }

  // ---------- menus ----------
  listMenus(ctx: TenantContext, venueId: string) { return this.repo.menus(ctx, venueId); }
  async getMenu(ctx: TenantContext, venueId: string, id: string): Promise<MenuRow> {
    const m = await this.repo.getMenu(ctx, id);
    if (!m || m.venue_id !== venueId || m.deleted_at) throw notFound('menu');
    return m;
  }
  createMenu(ctx: TenantContext, venueId: string, input: { name: string; schedule?: unknown; is_active?: boolean }) {
    return this.repo.insertMenu(ctx, {
      id: uuidv7(), organization_id: ctx.organizationId, venue_id: venueId, name: input.name,
      schedule: input.schedule ?? null, is_active: input.is_active ?? true, source: 'manual', external_id: null, deleted_at: null,
    });
  }
  async updateMenu(ctx: TenantContext, venueId: string, id: string, patch: Partial<Pick<MenuRow, 'name' | 'schedule' | 'is_active'>>) {
    await this.getMenu(ctx, venueId, id);
    return (await this.repo.updateMenu(ctx, id, patch))!;
  }
  async deleteMenu(ctx: TenantContext, venueId: string, id: string) {
    const m = await this.getMenu(ctx, venueId, id);
    if (m.source !== 'manual') throw new AppError('MENU_MANAGED_BY_POS', 409, 'Menu is synced from POS');
    await this.repo.updateMenu(ctx, id, { deleted_at: nowIso() });
  }

  // ---------- categories ----------
  async listCategories(ctx: TenantContext, venueId: string): Promise<CategoryRow[]> {
    const menus = await this.repo.menus(ctx, venueId);
    return (await this.repo.categories(ctx, menus.map((m) => m.id))).sort((a, b) => a.sort - b.sort || a.name.localeCompare(b.name));
  }
  async getCategory(ctx: TenantContext, venueId: string, id: string): Promise<CategoryRow> {
    const c = await this.repo.getCategory(ctx, id);
    if (!c || c.deleted_at) throw notFound('category');
    await this.getMenu(ctx, venueId, c.menu_id);
    return c;
  }
  async createCategory(ctx: TenantContext, venueId: string, input: { menu_id: string; parent_id?: string | null; name: string; sort?: number; is_visible?: boolean }) {
    await this.getMenu(ctx, venueId, input.menu_id);
    if (input.parent_id) {
      const p = await this.getCategory(ctx, venueId, input.parent_id);
      if (p.menu_id !== input.menu_id) throw new AppError('VALIDATION_FAILED', 400, 'parent_id belongs to another menu');
    }
    return this.repo.insertCategory(ctx, {
      id: uuidv7(), organization_id: ctx.organizationId, menu_id: input.menu_id, parent_id: input.parent_id ?? null,
      name: input.name, sort: input.sort ?? 0, is_visible: input.is_visible ?? true, external_id: null, deleted_at: null,
    });
  }
  async updateCategory(ctx: TenantContext, venueId: string, id: string, patch: { parent_id?: string | null; name?: string; sort?: number; is_visible?: boolean }) {
    const c = await this.getCategory(ctx, venueId, id);
    if (patch.parent_id) {
      if (patch.parent_id === id) throw new AppError('VALIDATION_FAILED', 400, 'category cannot be its own parent');
      // walk ancestors to prevent cycles
      let cur: CategoryRow | null = await this.getCategory(ctx, venueId, patch.parent_id);
      if (cur.menu_id !== c.menu_id) throw new AppError('VALIDATION_FAILED', 400, 'parent_id belongs to another menu');
      while (cur?.parent_id) {
        if (cur.parent_id === id) throw new AppError('VALIDATION_FAILED', 400, 'category hierarchy cycle');
        cur = await this.repo.getCategory(ctx, cur.parent_id);
      }
    }
    if (c.external_id && patch.name !== undefined && patch.name !== c.name) throw new AppError('MENU_MANAGED_BY_POS', 409, 'Category is synced from POS');
    return (await this.repo.updateCategory(ctx, id, patch))!;
  }
  async deleteCategory(ctx: TenantContext, venueId: string, id: string) {
    const c = await this.getCategory(ctx, venueId, id);
    if (c.external_id) throw new AppError('MENU_MANAGED_BY_POS', 409, 'Category is synced from POS');
    const items = (await this.repo.items(ctx, venueId)).filter((i) => i.category_id === id && !i.deleted_at);
    const children = (await this.listCategories(ctx, venueId)).filter((x) => x.parent_id === id);
    if (items.length || children.length) throw new AppError('CONFLICT', 409, 'Category is not empty');
    await this.repo.updateCategory(ctx, id, { deleted_at: nowIso() });
  }

  // ---------- items ----------
  async getItem(ctx: TenantContext, venueId: string, id: string, includeDeleted = false): Promise<ItemRow> {
    const i = await this.repo.getItem(ctx, id);
    if (!i || i.venue_id !== venueId || (i.deleted_at && !includeDeleted)) throw notFound('item');
    return i;
  }
  async listItems(ctx: TenantContext, venueId: string, q: { category_id?: string; q?: string; updated_since?: string; include_deleted?: boolean; limit: number; cursor?: string }) {
    let rows = await this.repo.items(ctx, venueId);
    if (!q.include_deleted && !q.updated_since) rows = rows.filter((r) => !r.deleted_at);
    if (q.category_id) rows = rows.filter((r) => r.category_id === q.category_id);
    if (q.q) { const n = q.q.toLowerCase(); rows = rows.filter((r) => r.name.toLowerCase().includes(n) || r.description?.toLowerCase().includes(n)); }
    if (q.updated_since) { const t = Date.parse(q.updated_since); rows = rows.filter((r) => Date.parse(r.updated_at ?? r.created_at) >= t); }
    rows.sort((a, b) => a.sort - b.sort || a.id.localeCompare(b.id));
    const offset = q.cursor ? Number(q.cursor) : 0;
    const page = rows.slice(offset, offset + q.limit);
    const next = offset + q.limit < rows.length ? String(offset + q.limit) : null;
    return { data: await Promise.all(page.map((r) => this.toAdminItem(ctx, r))), page: { next_cursor: next, has_more: next !== null } };
  }
  async createItem(ctx: TenantContext, venueId: string, input: ItemCreate) {
    const cat = await this.getCategory(ctx, venueId, input.category_id);
    const menu = await this.getMenu(ctx, venueId, cat.menu_id);
    if (menu.source !== 'manual') throw new AppError('MENU_MANAGED_BY_POS', 409, 'Menu is synced from POS; create items in the POS');
    await this.assertUpsell(ctx, venueId, input.upsell_item_ids);
    const ts = nowIso();
    const row = await this.repo.insertItem(ctx, {
      id: uuidv7(), organization_id: ctx.organizationId, venue_id: venueId, category_id: input.category_id,
      name: input.name, description: input.description ?? null, price: input.price,
      weight_g: input.weight_g ?? null, volume_ml: input.volume_ml ?? null, kcal: input.kcal ?? null, nutrition: null,
      tags: input.tags ?? [], upsell_item_ids: input.upsell_item_ids ?? [], vat_rate: input.vat_rate ?? 'none',
      marking_required: input.marking_required ?? false, image_file_id: input.image_file_id ?? null, sort: input.sort ?? 0,
      is_visible: !(input.hidden ?? false), external_source: null, external_id: null, version: 1,
      created_at: ts, updated_at: ts, deleted_at: null,
    });
    if (input.allergens || input.may_contain) await this.writeAllergens(ctx, row.id, input.allergens ?? [], input.may_contain ?? []);
    return this.toAdminItem(ctx, row);
  }
  async updateItem(ctx: TenantContext, venueId: string, id: string, patch: ItemPatch, ifMatchVersion?: number) {
    const cur = await this.getItem(ctx, venueId, id);
    if (ifMatchVersion !== undefined && ifMatchVersion !== cur.version) throw new AppError('CONFLICT', 412, 'Item was modified (If-Match mismatch)');
    if (cur.external_source) {
      const bad = POS_OWNED_FIELDS.filter((f) => patch[f] !== undefined && patch[f] !== cur[f]);
      if (bad.length) throw new AppError('MENU_MANAGED_BY_POS', 409, `Fields managed by POS: ${bad.join(', ')}`);
    }
    if (patch.category_id && patch.category_id !== cur.category_id) await this.getCategory(ctx, venueId, patch.category_id);
    await this.assertUpsell(ctx, venueId, patch.upsell_item_ids, id);
    const { hidden, allergens, may_contain, ...rest } = patch;
    const upd: Partial<ItemRow> = { ...rest, version: cur.version + 1, updated_at: nowIso() };
    if (hidden !== undefined) upd.is_visible = !hidden;
    const row = (await this.repo.updateItem(ctx, id, upd))!;
    if (allergens || may_contain) {
      const existing = await this.allergenCodes(ctx, id);
      await this.writeAllergens(ctx, id, allergens ?? existing.contains, may_contain ?? existing.mayContain);
    }
    return this.toAdminItem(ctx, row);
  }
  async deleteItem(ctx: TenantContext, venueId: string, id: string) {
    const cur = await this.getItem(ctx, venueId, id);
    if (cur.external_source) throw new AppError('MENU_MANAGED_BY_POS', 409, 'Item is synced from POS');
    await this.repo.updateItem(ctx, id, { deleted_at: nowIso(), version: cur.version + 1, updated_at: nowIso() });
  }
  private async assertUpsell(ctx: TenantContext, venueId: string, ids?: string[], self?: string) {
    for (const u of ids ?? []) {
      if (u === self) throw new AppError('VALIDATION_FAILED', 400, 'item cannot upsell itself');
      await this.getItem(ctx, venueId, u).catch(() => { throw new AppError('VALIDATION_FAILED', 400, `upsell item ${u} not found`); });
    }
  }
  private async writeAllergens(ctx: TenantContext, itemId: string, contains: string[], mayContain: string[]) {
    const list = [...new Set(contains)].map((code) => ({ code, may_contain: false }));
    for (const code of new Set(mayContain)) if (!contains.includes(code)) list.push({ code, may_contain: true });
    await this.repo.setItemAllergens(ctx, itemId, list);
  }
  async allergenCodes(ctx: TenantContext, itemId: string) {
    const links = await this.repo.itemAllergens(ctx, itemId);
    const contains: AllergenCode[] = []; const mayContain: AllergenCode[] = [];
    for (const l of links) {
      const a = await this.repo.allergenById(ctx, l.allergen_id);
      if (a) (l.may_contain ? mayContain : contains).push(a.code as AllergenCode);
    }
    return { contains: contains.sort(), mayContain: mayContain.sort() };
  }
  async getAdminItem(ctx: TenantContext, venueId: string, id: string) { return this.toAdminItem(ctx, await this.getItem(ctx, venueId, id)); }
  private async toAdminItem(ctx: TenantContext, r: ItemRow) {
    const { contains, mayContain } = await this.allergenCodes(ctx, r.id);
    const groups = (await this.repo.itemGroups(ctx, r.id)).sort((a, b) => a.sort - b.sort).map((g) => g.group_id);
    return { ...r, hidden: !r.is_visible, allergens: contains, may_contain: mayContain, modifier_group_ids: groups, photo_url: this.photoUrl(r.image_file_id) };
  }
  private photoUrl(fileId: string | null) { return fileId ? `${this.mediaBaseUrl}/files/${fileId}` : null; }

  // ---------- modifiers ----------
  async listGroups(ctx: TenantContext, venueId: string) {
    const gs = await this.repo.groups(ctx, venueId);
    return Promise.all(gs.map(async (g) => ({ ...g, modifiers: (await this.repo.modifiers(ctx, g.id)).sort((a, b) => a.sort - b.sort) })));
  }
  async getGroup(ctx: TenantContext, venueId: string, id: string): Promise<GroupRow> {
    const g = await this.repo.getGroup(ctx, id);
    if (!g || g.venue_id !== venueId) throw notFound('modifier group');
    return g;
  }
  createGroup(ctx: TenantContext, venueId: string, input: { name: string; min_select: number; max_select: number }) {
    return this.repo.insertGroup(ctx, { id: uuidv7(), organization_id: ctx.organizationId, venue_id: venueId, ...input, external_id: null, deleted_at: null });
  }
  async updateGroup(ctx: TenantContext, venueId: string, id: string, patch: { name?: string; min_select?: number; max_select?: number }) {
    const g = await this.getGroup(ctx, venueId, id);
    if ((patch.min_select ?? g.min_select) > (patch.max_select ?? g.max_select)) throw new AppError('VALIDATION_FAILED', 400, 'min_select must be <= max_select');
    return (await this.repo.updateGroup(ctx, id, patch))!;
  }
  async deleteGroup(ctx: TenantContext, venueId: string, id: string) {
    const g = await this.getGroup(ctx, venueId, id);
    if (g.external_id) throw new AppError('MENU_MANAGED_BY_POS', 409, 'Modifier group is synced from POS');
    await this.repo.updateGroup(ctx, id, { deleted_at: nowIso() });
  }
  async createModifier(ctx: TenantContext, venueId: string, groupId: string, input: { name: string; price_delta: number; sort?: number }) {
    await this.getGroup(ctx, venueId, groupId);
    return this.repo.insertModifier(ctx, { id: uuidv7(), organization_id: ctx.organizationId, group_id: groupId, name: input.name, price_delta: input.price_delta, sort: input.sort ?? 0, external_id: null, deleted_at: null });
  }
  async getModifier(ctx: TenantContext, venueId: string, id: string): Promise<ModifierRow> {
    const m = await this.repo.getModifier(ctx, id);
    if (!m) throw notFound('modifier');
    await this.getGroup(ctx, venueId, m.group_id);
    return m;
  }
  async updateModifier(ctx: TenantContext, venueId: string, id: string, patch: { name?: string; price_delta?: number; sort?: number }) {
    const m = await this.getModifier(ctx, venueId, id);
    if (m.external_id && ((patch.price_delta !== undefined && patch.price_delta !== m.price_delta) || (patch.name !== undefined && patch.name !== m.name)))
      throw new AppError('MENU_MANAGED_BY_POS', 409, 'Modifier is synced from POS');
    return (await this.repo.updateModifier(ctx, id, patch))!;
  }
  async deleteModifier(ctx: TenantContext, venueId: string, id: string) {
    const m = await this.getModifier(ctx, venueId, id);
    if (m.external_id) throw new AppError('MENU_MANAGED_BY_POS', 409, 'Modifier is synced from POS');
    await this.repo.updateModifier(ctx, id, { deleted_at: nowIso() });
  }
  async setItemGroups(ctx: TenantContext, venueId: string, itemId: string, groupIds: string[]) {
    const item = await this.getItem(ctx, venueId, itemId);
    const uniq = [...new Set(groupIds)];
    for (const g of uniq) await this.getGroup(ctx, venueId, g);
    await this.repo.setItemGroups(ctx, itemId, uniq);
    await this.repo.updateItem(ctx, itemId, { version: item.version + 1, updated_at: nowIso() });
    return this.toAdminItem(ctx, (await this.repo.getItem(ctx, itemId))!);
  }

  // ---------- translations ----------
  async putTranslations(ctx: TenantContext, venueId: string, items: { entity_type: string; entity_id: string; field: string; locale: string; text: string; status: 'machine' | 'reviewed' }[]) {
    const out: TranslationRow[] = [];
    for (const t of items) {
      await this.assertEntity(ctx, venueId, t.entity_type, t.entity_id);
      out.push(await this.repo.upsertTranslation(ctx, { ...t, source_hash: null }));
    }
    return out;
  }
  async listTranslations(ctx: TenantContext, venueId: string, entityType: string, entityId: string) {
    await this.assertEntity(ctx, venueId, entityType, entityId);
    return this.repo.translations(ctx, { entity_id: entityId });
  }
  private async assertEntity(ctx: TenantContext, venueId: string, type: string, id: string) {
    if (type === 'menu_item') await this.getItem(ctx, venueId, id);
    else if (type === 'menu_category') await this.getCategory(ctx, venueId, id);
    else if (type === 'modifier_group') await this.getGroup(ctx, venueId, id);
    else if (type === 'modifier') await this.getModifier(ctx, venueId, id);
    else throw new AppError('VALIDATION_FAILED', 400, `unknown entity_type ${type}`);
  }

  // ---------- stop-list ----------
  async activeStopList(ctx: TenantContext, venueId: string): Promise<StopRow[]> {
    const now = this.now();
    return (await this.repo.stopEntries(ctx, venueId)).filter((e) => isStopActive(e, now));
  }
  private async assertStopTarget(ctx: TenantContext, venueId: string, e: StopListInput) {
    if (e.item_id) await this.getItem(ctx, venueId, e.item_id);
    else await this.getModifier(ctx, venueId, e.modifier_id!);
  }
  async addStop(ctx: TenantContext, venueId: string, e: StopListInput, userId: string | null, source: StopRow['source'] = 'manual') {
    await this.assertStopTarget(ctx, venueId, e);
    return this.repo.insertStop(ctx, {
      id: uuidv7(), organization_id: ctx.organizationId, venue_id: venueId, item_id: e.item_id ?? null,
      modifier_id: e.modifier_id ?? null, remaining_qty: e.balance ?? null, reason: e.reason ?? null, source,
      created_by: userId, valid_from: new Date(this.now()).toISOString(), valid_until: e.valid_until ?? null, lifted_at: null,
    });
  }
  /** Replaces all active entries of `source` (manual by default; POS sync passes 'pos'). */
  async replaceStopList(ctx: TenantContext, venueId: string, entries: StopListInput[], userId: string | null, source: StopRow['source'] = 'manual') {
    for (const e of entries) await this.assertStopTarget(ctx, venueId, e);
    const liftedAt = new Date(this.now()).toISOString();
    for (const e of await this.activeStopList(ctx, venueId)) if (e.source === source) await this.repo.updateStop(ctx, e.id, { lifted_at: liftedAt });
    const out: StopRow[] = [];
    for (const e of entries) out.push(await this.addStop(ctx, venueId, e, userId, source));
    return out;
  }
  async liftStop(ctx: TenantContext, venueId: string, id: string) {
    const e = await this.repo.getStop(ctx, id);
    if (!e || e.venue_id !== venueId) throw notFound('stop-list entry');
    if (e.lifted_at) return e;
    return (await this.repo.updateStop(ctx, id, { lifted_at: new Date(this.now()).toISOString() }))!;
  }

  /** For the orders module: which of the requested items/modifiers cannot be sold now. */
  async checkAvailability(ctx: TenantContext, venueId: string, lines: { item_id: string; modifier_ids?: string[] }[]) {
    const stops = await this.activeStopList(ctx, venueId);
    const unavailable: { item_id: string; modifier_id?: string; reason: 'NOT_FOUND' | 'HIDDEN' | 'STOP_LIST' }[] = [];
    for (const l of lines) {
      const item = await this.repo.getItem(ctx, l.item_id);
      if (!item || item.venue_id !== venueId || item.deleted_at) { unavailable.push({ item_id: l.item_id, reason: 'NOT_FOUND' }); continue; }
      if (!item.is_visible) { unavailable.push({ item_id: l.item_id, reason: 'HIDDEN' }); continue; }
      if (!availabilityOf(stops.filter((s) => s.item_id === item.id)).available) unavailable.push({ item_id: l.item_id, reason: 'STOP_LIST' });
      for (const m of l.modifier_ids ?? [])
        if (!availabilityOf(stops.filter((s) => s.modifier_id === m)).available) unavailable.push({ item_id: l.item_id, modifier_id: m, reason: 'STOP_LIST' });
    }
    return unavailable;
  }

  // ---------- menu assembly ----------
  /**
   * Builds the menu tree. Guest view (`includeHidden=false`): only active menus, visible categories/items,
   * empty categories dropped, stop-list applied as `available=false`. Version = content hash (ETag).
   * Menu `schedule` is not evaluated yet (all active menus are shown) — see README.
   */
  async buildMenu(ctx: TenantContext, venueId: string, locale: string, includeHidden = false): Promise<PublicMenu> {
    const menus = (await this.repo.menus(ctx, venueId)).filter((m) => includeHidden || m.is_active);
    const cats = (await this.repo.categories(ctx, menus.map((m) => m.id))).filter((c) => includeHidden || c.is_visible);
    const items = (await this.repo.items(ctx, venueId)).filter((i) => !i.deleted_at && (includeHidden || i.is_visible));
    const stops = await this.activeStopList(ctx, venueId);
    const tr = locale === DEFAULT_LOCALE ? new Map<string, string>()
      : new Map((await this.repo.translations(ctx, { locale })).map((t) => [`${t.entity_id}:${t.field}`, t.text]));
    const t = (id: string, field: string, base: string) => tr.get(`${id}:${field}`) ?? base;

    const groupCache = new Map<string, PublicModifierGroup | null>();
    const groupDto = async (gid: string) => {
      if (!groupCache.has(gid)) {
        const g = await this.repo.getGroup(ctx, gid);
        groupCache.set(gid, g ? {
          id: g.id, name: t(g.id, 'name', g.name), min: g.min_select, max: g.max_select,
          options: (await this.repo.modifiers(ctx, g.id)).sort((a, b) => a.sort - b.sort).map((m) => ({
            id: m.id, name: t(m.id, 'name', m.name), price: m.price_delta,
            available: availabilityOf(stops.filter((s) => s.modifier_id === m.id)).available,
          })),
        } : null);
      }
      return groupCache.get(gid)!;
    };

    const visibleCatIds = new Set(cats.map((c) => c.id));
    const byCat = new Map<string, PublicMenuItem[]>();
    for (const i of items.sort((a, b) => a.sort - b.sort || a.name.localeCompare(b.name))) {
      if (!visibleCatIds.has(i.category_id)) continue;
      const av = availabilityOf(stops.filter((s) => s.item_id === i.id));
      const groups: PublicModifierGroup[] = [];
      for (const link of (await this.repo.itemGroups(ctx, i.id)).sort((a, b) => a.sort - b.sort)) {
        const g = await groupDto(link.group_id); if (g) groups.push(g);
      }
      const dto: PublicMenuItem = {
        id: i.id, external_id: i.external_id, category_id: i.category_id, name: t(i.id, 'name', i.name),
        description: t(i.id, 'description', i.description ?? ''), price: i.price, weight_g: i.weight_g, volume_ml: i.volume_ml,
        kcal: i.kcal, allergens: (await this.allergenCodes(ctx, i.id)).contains, tags: i.tags, photo_url: this.photoUrl(i.image_file_id),
        available: av.available, remaining_qty: av.remaining, modifier_groups: groups, updated_at: i.updated_at ?? i.created_at,
      };
      const list = byCat.get(i.category_id) ?? []; list.push(dto); byCat.set(i.category_id, list);
    }
    const menuOrder = new Map(menus.map((m, idx) => [m.id, idx]));
    const categories: PublicMenuCategory[] = cats
      .sort((a, b) => menuOrder.get(a.menu_id)! - menuOrder.get(b.menu_id)! || a.sort - b.sort || a.name.localeCompare(b.name))
      .map((c) => ({ id: c.id, parent_id: c.parent_id, name: t(c.id, 'name', c.name), items: byCat.get(c.id) ?? [] }))
      ;
    // keep a category if it or any descendant has items (guest view)
    const keep = new Set<string>();
    const parentOf = new Map(categories.map((c) => [c.id, c.parent_id]));
    for (const c of categories) if (c.items.length) for (let id: string | null | undefined = c.id; id && !keep.has(id); id = parentOf.get(id)) keep.add(id);
    const shown = includeHidden ? categories : categories.filter((c) => keep.has(c.id));
    const body = { locale, venue_id: venueId, categories: shown };
    const version = createHash('sha256').update(JSON.stringify(body)).digest('base64url').slice(0, 22);
    return { version, ...body };
  }
}

const services = new WeakMap<Db, MenuService>();
/** Shared per-Db instance for other modules (Fastify encapsulation hides plugin decorators from siblings). */
export function getMenuService(db: Db): MenuService {
  let s = services.get(db); if (!s) services.set(db, (s = new MenuService(db))); return s;
}
