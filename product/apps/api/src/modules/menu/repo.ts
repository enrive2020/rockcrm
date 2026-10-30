import type { Db, Row, TenantContext } from '@podacha/db';
import { uuidv7 } from '@podacha/shared';
import type {
  Menu, MenuCategory, MenuItem, ModifierGroup, Modifier, ItemModifierGroup,
  Allergen, ItemAllergen, StopListEntry, Translation,
} from '@podacha/shared';

/**
 * Data access for the menu module. Only this module reads these tables.
 * Notes (see README.md in this folder):
 *  - `Repo` has no DELETE, so link rows (item_modifier_group, item_allergen) and rows of tables
 *    without `deleted_at` (modifier_group, modifier) are tombstoned with `deleted_at`.
 *    Migration 00xx_menu_tombstones.sql adds those columns + surrogate `id` on link tables.
 *  - pg `Repo.list` caps at 200 rows; LIST_LIMIT is the module-wide ceiling.
 */
export const LIST_LIMIT = 10_000;

type WithRow<T> = T & Row;
export type MenuRow = WithRow<Menu>;
export type CategoryRow = WithRow<MenuCategory>;
export type ItemRow = WithRow<MenuItem>;
export type GroupRow = WithRow<ModifierGroup> & { deleted_at?: string | null };
export type ModifierRow = WithRow<Modifier> & { deleted_at?: string | null };
export type ItemGroupRow = WithRow<ItemModifierGroup> & { deleted_at?: string | null };
export type AllergenRow = WithRow<Allergen>;
export type ItemAllergenRow = WithRow<ItemAllergen> & { deleted_at?: string | null };
export type StopRow = WithRow<StopListEntry>;
export type TranslationRow = Row & Omit<Translation, 'source_hash'> & { source_hash?: Uint8Array | null };
export type TranslationInput = Omit<Translation, 'id' | 'organization_id' | 'source_hash'> & { source_hash?: Uint8Array | null };

const alive = <T extends { deleted_at?: string | null }>(r: T) => !r.deleted_at;

export class MenuRepo {
  constructor(private readonly db: Db) {}
  private r<T extends Row>(t: string) { return this.db.repo<T>(t); }

  menus = (ctx: TenantContext, venueId: string) =>
    this.r<MenuRow>('menu').list(ctx, { venue_id: venueId }, LIST_LIMIT).then((x) => x.filter(alive));
  getMenu = (ctx: TenantContext, id: string) => this.r<MenuRow>('menu').get(ctx, id);
  insertMenu = (ctx: TenantContext, row: MenuRow) => this.r<MenuRow>('menu').insert(ctx, row);
  updateMenu = (ctx: TenantContext, id: string, p: Partial<MenuRow>) => this.r<MenuRow>('menu').update(ctx, id, p);

  async categories(ctx: TenantContext, menuIds: string[]): Promise<CategoryRow[]> {
    const out: CategoryRow[] = [];
    for (const menu_id of menuIds) out.push(...await this.r<CategoryRow>('menu_category').list(ctx, { menu_id }, LIST_LIMIT));
    return out.filter(alive);
  }
  getCategory = (ctx: TenantContext, id: string) => this.r<CategoryRow>('menu_category').get(ctx, id);
  insertCategory = (ctx: TenantContext, row: CategoryRow) => this.r<CategoryRow>('menu_category').insert(ctx, row);
  updateCategory = (ctx: TenantContext, id: string, p: Partial<CategoryRow>) => this.r<CategoryRow>('menu_category').update(ctx, id, p);

  items = (ctx: TenantContext, venueId: string) => this.r<ItemRow>('menu_item').list(ctx, { venue_id: venueId }, LIST_LIMIT);
  getItem = (ctx: TenantContext, id: string) => this.r<ItemRow>('menu_item').get(ctx, id);
  insertItem = (ctx: TenantContext, row: ItemRow) => this.r<ItemRow>('menu_item').insert(ctx, row);
  updateItem = (ctx: TenantContext, id: string, p: Partial<ItemRow>) => this.r<ItemRow>('menu_item').update(ctx, id, p);

  groups = (ctx: TenantContext, venueId: string) =>
    this.r<GroupRow>('modifier_group').list(ctx, { venue_id: venueId }, LIST_LIMIT).then((x) => x.filter(alive));
  getGroup = (ctx: TenantContext, id: string) => this.r<GroupRow>('modifier_group').get(ctx, id).then((g) => (g && alive(g) ? g : null));
  insertGroup = (ctx: TenantContext, row: GroupRow) => this.r<GroupRow>('modifier_group').insert(ctx, row);
  updateGroup = (ctx: TenantContext, id: string, p: Partial<GroupRow>) => this.r<GroupRow>('modifier_group').update(ctx, id, p);

  modifiers = (ctx: TenantContext, groupId: string) =>
    this.r<ModifierRow>('modifier').list(ctx, { group_id: groupId }, LIST_LIMIT).then((x) => x.filter(alive));
  getModifier = (ctx: TenantContext, id: string) => this.r<ModifierRow>('modifier').get(ctx, id).then((m) => (m && alive(m) ? m : null));
  insertModifier = (ctx: TenantContext, row: ModifierRow) => this.r<ModifierRow>('modifier').insert(ctx, row);
  updateModifier = (ctx: TenantContext, id: string, p: Partial<ModifierRow>) => this.r<ModifierRow>('modifier').update(ctx, id, p);

  itemGroups = (ctx: TenantContext, itemId: string) =>
    this.r<ItemGroupRow>('item_modifier_group').list(ctx, { item_id: itemId }, LIST_LIMIT).then((x) => x.filter(alive));
  async setItemGroups(ctx: TenantContext, itemId: string, groupIds: string[]): Promise<void> {
    const repo = this.r<ItemGroupRow>('item_modifier_group');
    const existing = await repo.list(ctx, { item_id: itemId }, LIST_LIMIT);
    for (const e of existing) if (!e.deleted_at && !groupIds.includes(e.group_id)) await repo.update(ctx, e.id, { deleted_at: new Date().toISOString() });
    for (const [sort, group_id] of groupIds.entries()) {
      const id = `${itemId}:${group_id}`;
      const ex = existing.find((e) => e.id === id);
      if (ex) await repo.update(ctx, id, { deleted_at: null, sort });
      else await repo.insert(ctx, { id, item_id: itemId, group_id, organization_id: ctx.organizationId, sort, deleted_at: null });
    }
  }

  async ensureAllergen(ctx: TenantContext, code: string): Promise<AllergenRow> {
    const repo = this.r<AllergenRow>('allergen');
    const [found] = await repo.list(ctx, { code }, 1);
    if (found) return found;
    return repo.insert(ctx, { id: `alg:${ctx.organizationId}:${code}`, organization_id: ctx.organizationId, code, name: code });
  }
  allergenById = (ctx: TenantContext, id: string) => this.r<AllergenRow>('allergen').get(ctx, id);
  itemAllergens = (ctx: TenantContext, itemId: string) =>
    this.r<ItemAllergenRow>('item_allergen').list(ctx, { item_id: itemId }, LIST_LIMIT).then((x) => x.filter(alive));
  async setItemAllergens(ctx: TenantContext, itemId: string, codes: { code: string; may_contain: boolean }[]): Promise<void> {
    const repo = this.r<ItemAllergenRow>('item_allergen');
    const existing = await repo.list(ctx, { item_id: itemId }, LIST_LIMIT);
    const wanted = new Map<string, boolean>();
    for (const c of codes) wanted.set((await this.ensureAllergen(ctx, c.code)).id, c.may_contain);
    for (const e of existing) if (!e.deleted_at && !wanted.has(e.allergen_id)) await repo.update(ctx, e.id, { deleted_at: new Date().toISOString() });
    for (const [allergen_id, may_contain] of wanted) {
      const id = `${itemId}:${allergen_id}`;
      if (existing.some((e) => e.id === id)) await repo.update(ctx, id, { deleted_at: null, may_contain });
      else await repo.insert(ctx, { id, item_id: itemId, allergen_id, organization_id: ctx.organizationId, may_contain, deleted_at: null });
    }
  }

  stopEntries = (ctx: TenantContext, venueId: string) => this.r<StopRow>('stop_list_entry').list(ctx, { venue_id: venueId }, LIST_LIMIT);
  getStop = (ctx: TenantContext, id: string) => this.r<StopRow>('stop_list_entry').get(ctx, id);
  insertStop = (ctx: TenantContext, row: StopRow) => this.r<StopRow>('stop_list_entry').insert(ctx, row);
  updateStop = (ctx: TenantContext, id: string, p: Partial<StopRow>) => this.r<StopRow>('stop_list_entry').update(ctx, id, p);

  translations = (ctx: TenantContext, where: Partial<Pick<Translation, 'entity_id' | 'locale' | 'field'>>) => this.r<TranslationRow>('translation').list(ctx, where, LIST_LIMIT);
  async upsertTranslation(ctx: TenantContext, t: TranslationInput): Promise<TranslationRow> {
    const repo = this.r<TranslationRow>('translation');
    const [ex] = await repo.list(ctx, { entity_id: t.entity_id, field: t.field, locale: t.locale }, 1);
    if (ex) return (await repo.update(ctx, ex.id, { text: t.text, status: t.status }))!;
    return repo.insert(ctx, { ...t, id: uuidv7(), organization_id: ctx.organizationId } as TranslationRow);
  }
}
